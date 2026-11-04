"""Measure how real superintendents schedule, from parsed judging programs.

    python -m optdog.analysis.calibrate        # writes docs/calibration.md

Every number here comes from the published programs in data/parsed/. Planned
durations use AKC's 25 dogs/hour (2.4 min/dog; 3.0 for permit judges).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from optdog.domain import VARIETY_GROUPS, EventKind, JudgingProgram, fmt_time
from optdog.io.program_csv import read_program
from optdog.params import ShowParameters
from optdog.validate import _conformation_blocks, validate

ROOT = Path(__file__).resolve().parents[3]
PARSED = ROOT / "data" / "parsed"
OUT = ROOT / "docs" / "calibration.md"


def q(xs, p):
    """p-th percentile (0–100), nearest-rank."""
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def dist(xs, fmt="{:.1f}") -> str:
    if not xs:
        return "–"
    vals = [q(xs, 10), q(xs, 50), q(xs, 90)]
    return f"n={len(xs)}; p10 {fmt.format(vals[0])} · median {fmt.format(vals[1])} · p90 {fmt.format(vals[2])} · max {fmt.format(max(xs))}"


@dataclass
class Measurements:
    shows: list[tuple[str, JudgingProgram]] = field(default_factory=list)
    implied_min_per_dog: list[float] = field(default_factory=list)  # published gap / entries, same judge+ring
    block_entries: list[int] = field(default_factory=list)  # non-final multi-breed blocks
    block_published_minutes: list[float] = field(default_factory=list)  # gap to the same judge's next block
    over_hour_minutes: list[float] = field(default_factory=list)  # planned duration − 60, for non-exempt blocks > 60
    overlaps: list[float] = field(default_factory=list)  # H3/H4 shortfall in minutes
    lunch_starts: list[int] = field(default_factory=list)
    lunch_lengths: list[float] = field(default_factory=list)
    final_segment_entries: list[int] = field(default_factory=list)
    judge_loads: list[int] = field(default_factory=list)
    judge_work_minutes: list[float] = field(default_factory=list)
    group_ring_slack: list[float] = field(default_factory=list)  # group start − group ring free
    group_start: list[int] = field(default_factory=list)
    judging_start: list[int] = field(default_factory=list)
    group_breeds_done_vs_start: dict = field(default_factory=lambda: defaultdict(list))
    first_group_slack: list[float] = field(default_factory=list)
    violations: Counter = field(default_factory=Counter)
    shows_with: Counter = field(default_factory=Counter)
    violations_strict: Counter = field(default_factory=Counter)  # overlap tolerance 0
    rows: list[dict] = field(default_factory=list)


def measure(programs: list[tuple[str, JudgingProgram]]) -> Measurements:
    m = Measurements(shows=programs)
    for name, prog in programs:
        blocks = _conformation_blocks(prog)
        by_ring, by_judge = defaultdict(list), defaultdict(list)
        for sb in blocks:
            by_ring[sb.ring].append(sb)
            by_judge[sb.judge].append(sb)
        for seq in list(by_ring.values()) + list(by_judge.values()):
            seq.sort(key=lambda b: b.start)

        m.judging_start.append(min(sb.start for sb in blocks))
        # published gap between consecutive blocks of the same judge in the same ring
        for judge, seq in by_judge.items():
            for a, b in zip(seq, seq[1:]):
                if a.ring == b.ring and a.block.entries > 0:
                    gap = b.start - a.start
                    if gap < 45 + a.block.duration:  # exclude lunch / long idle gaps
                        m.implied_min_per_dog.append(gap / a.block.entries)
                        m.block_published_minutes.append(gap)
            m.judge_work_minutes.append(sum(sb.block.duration for sb in seq))
        for ring, seq in by_ring.items():
            m.final_segment_entries.append(seq[-1].block.entries)
            for sb in seq[:-1]:
                b = sb.block
                if len(b.events) > 1:
                    m.block_entries.append(b.entries)
                    if b.duration > 60:
                        m.over_hour_minutes.append(b.duration - 60)
        for br in prog.breaks:
            if br.minutes is not None:
                m.lunch_starts.append(br.start)
                m.lunch_lengths.append(br.minutes)
        for j in prog.show.judges:
            m.judge_loads.append(prog.show.judge_load(j))

        res = validate(prog)
        codes = Counter(v.code for v in res.violations)
        m.violations_strict.update(v.code for v in validate(prog, ShowParameters(overlap_tolerance_minutes=0)).violations)
        m.violations.update(codes)
        for c in codes:
            m.shows_with[c] += 1
        m.overlaps += [v.amount for v in res.violations if v.code in ("H3", "H4")]
        tl, plan = res.timeline, prog.groups
        if tl and plan:
            m.group_start.append(plan.start)
            if res.metrics.get("group_ring_slack_min") is not None:
                m.group_ring_slack.append(res.metrics["group_ring_slack_min"])
            for s in tl.groups:
                m.group_breeds_done_vs_start[s.group].append(s.breeds_done - plan.start)
            m.first_group_slack.append(plan.start - tl.groups[0].breeds_done)
        entries = sum(e.entries for e in prog.show.events if e.kind is EventKind.BREED and e.competition.value == "all-breed")
        m.rows.append(
            {
                "show": name,
                "breed entries": entries,
                "judges": len(by_judge),
                "rings": len(by_ring),
                "start": fmt_time(min(sb.start for sb in blocks)),
                "groups": fmt_time(plan.start) if plan else "?",
                "group rings": " & ".join(str(r.number) for r in plan.rings) if plan else "?",
                "violations": ", ".join(f"{k}×{v}" for k, v in sorted(codes.items())) or "none",
                **{k.split("_", 1)[0] + " " + k.split("_", 1)[1].replace("_", " "): v for k, v in res.soft.items()},
            }
        )
    return m


def render(m: Measurements) -> str:
    rate = m.implied_min_per_dog
    L = [
        "# Calibration: how real judging programs are scheduled",
        "",
        "Generated by `python -m optdog.analysis.calibrate` from the parsed programs in `data/parsed/` "
        f"({len(m.shows)} all-breed show days). Planned durations assume AKC's 25 dogs/hour "
        "(2.4 min/dog; 3.0 for permit judges; juniors 20/hour). Distributions show p10 · median · p90 · max.",
        "",
        "## Shows",
        "",
    ]
    cols = list(m.rows[0].keys())
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "---|" * len(cols))
    for r in m.rows:
        L.append("| " + " | ".join(str(r[c]) for c in cols) + " |")

    L += [
        "",
        "## Judging rate actually assumed",
        "",
        "Published gap between a judge's consecutive blocks in the same ring, divided by the earlier block's entries:",
        "",
        f"- minutes per dog: {dist(rate, '{:.2f}')}",
        f"- that is, dogs per hour at the median: **{60 / q(rate, 50):.1f}**" if rate else "",
        f"- share of blocks scheduled faster than 2.4 min/dog (25/hr): {sum(1 for x in rate if x < 2.4 - 1e-9) / len(rate):.0%}" if rate else "",
        f"- share faster than 2.0 min/dog (30/hr): {sum(1 for x in rate if x < 2.0 - 1e-9) / len(rate):.0%}" if rate else "",
        "",
        "## Block size",
        "",
        f"- multi-breed (non-final) blocks, entries: {dist(m.block_entries, '{:.0f}')}",
        f"- published block length (gap to judge's next block, same ring), minutes: {dist(m.block_published_minutes, '{:.0f}')}",
        f"- multi-breed blocks planned over 60 min at 25/hr, minutes over: {dist(m.over_hour_minutes, '{:.1f}')}",
        f"- final segments of a ring, entries: {dist(m.final_segment_entries, '{:.0f}')}",
        "",
        "## Overlaps (H3/H4) when blocks are timed at 25 dogs/hour",
        "",
        f"- minutes a block would still be running when the next one is published to start: {dist(m.overlaps, '{:.1f}')}",
        "",
        "## Lunch",
        "",
        f"- start: {dist(m.lunch_starts, '{:.0f}')} (minutes after midnight) — "
        + (f"p10 {fmt_time(q(m.lunch_starts, 10))}, median {fmt_time(q(m.lunch_starts, 50))}, p90 {fmt_time(q(m.lunch_starts, 90))}" if m.lunch_starts else ""),
        f"- length (to the judge's next block), minutes: {dist(m.lunch_lengths, '{:.0f}')}",
        "",
        "## Judges",
        "",
        f"- daily load (entries): {dist(m.judge_loads, '{:.0f}')}",
        f"- planned judging minutes at 25/hr: {dist(m.judge_work_minutes, '{:.0f}')}",
        "",
        "## Groups",
        "",
        f"- judging starts: " + ", ".join(sorted({fmt_time(t) for t in m.judging_start})),
        f"- first group published: {dist(m.group_start, '{:.0f}')} — "
        + (f"earliest {fmt_time(min(m.group_start))}, latest {fmt_time(max(m.group_start))}" if m.group_start else ""),
        f"- group ring(s) free before groups (group start − last breed block end in group rings), minutes: {dist(m.group_ring_slack, '{:.0f}')}",
        f"- first group's breeds finish before group start by, minutes: {dist(m.first_group_slack, '{:.0f}')}",
        "",
        "When each group's breeds finish, relative to the published group start (negative = finished before groups began):",
        "",
        "| group | " + " | ".join(["p10", "median", "p90", "max"]) + " |",
        "|---|---|---|---|---|",
    ]
    for g in VARIETY_GROUPS:
        xs = m.group_breeds_done_vs_start.get(g, [])
        if xs:
            L.append(f"| {g.value} | {q(xs, 10):.0f} | {q(xs, 50):.0f} | {q(xs, 90):.0f} | {max(xs):.0f} |")
    L += [
        "",
        "## Hard-constraint violations in human-made programs",
        "",
        f"Default parameters (overlap tolerance {ShowParameters().overlap_tolerance_minutes:.0f} min), and with zero tolerance:",
        "",
        "| code | total | shows | total, zero tolerance |",
        "|---|---|---|---|",
    ]
    for code in sorted(set(m.violations) | set(m.violations_strict)):
        L.append(f"| {code} | {m.violations[code]} | {m.shows_with[code]}/{len(m.shows)} | {m.violations_strict[code]} |")
    return "\n".join(x for x in L if x is not None) + "\n"


def load_all() -> list[tuple[str, JudgingProgram]]:
    out = []
    for d in sorted(PARSED.glob("*/*")):
        if (d / "schedule.csv").exists():
            out.append((d.name, read_program(d)))
    return out


def main() -> None:
    m = measure(load_all())
    OUT.write_text(render(m))
    print(OUT.read_text())


if __name__ == "__main__":
    main()
