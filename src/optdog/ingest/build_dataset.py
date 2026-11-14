"""Parse the raw judging programs listed in each manifest into data/parsed/.

    python -m optdog.ingest.build_dataset

Only regular breed events of the all-breed show are kept (U 2026-11-13): sweepstakes, Junior
Showmanship, Beginner Puppy, FSS open shows, concurrent specialties, obedience and rally are
dropped after parsing, and each report says how many. The parse itself, and its checks against
the program's printed totals and breed index, still see every event.
"""

from __future__ import annotations

import csv
import re
from dataclasses import replace
from pathlib import Path

from collections import Counter

from optdog.domain import Competition, EventKind, JudgingProgram, fmt_time
from optdog.ingest.bradshaw import BradshawParser, ParsedDay
from optdog.io.program_csv import write_program

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
PARSED = ROOT / "data" / "parsed"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def is_regular(e) -> bool:
    return e.kind is EventKind.BREED and e.competition is Competition.ALL_BREED


def regular_breed_only(program: JudgingProgram) -> tuple[JudgingProgram, Counter]:
    """``program`` without any event but regular breed events, and what was dropped by
    (kind, competition). A block left with no events goes; so do breaks of judges left with no blocks."""
    dropped = Counter((e.kind.value, e.competition.value) for e in program.show.events if not is_regular(e))
    blocks = []
    for sb in program.blocks:
        evs = tuple(e for e in sb.block.events if is_regular(e))
        if evs:
            blocks.append(sb if len(evs) == len(sb.block.events) else replace(sb, block=replace(sb.block, events=evs)))
    show = program.show
    judging = {sb.judge for sb in blocks}
    panel = judging | set(show.group_judges.values()) | ({show.bis_judge} if show.bis_judge else set())
    rings = {sb.ring for sb in blocks} | set(show.group_rings)
    view = replace(show, judges=[j for j in show.judges if j in judging],
                   events=[e for e in show.events if is_regular(e)],
                   rings=[r for r in show.rings if r in rings], entries=[],
                   start=min((sb.start for sb in blocks), default=show.start))  # when breed judging begins
    breaks = [br for br in program.breaks if br.judge in panel]
    return JudgingProgram(view, blocks, breaks, program.groups), dropped


def write_report(pd: ParsedDay, path: Path, dropped: Counter | None = None) -> None:
    r, prog = pd.report, pd.program
    show, plan = prog.show, prog.groups
    lines = [
        f"# {show.name} — {show.date}",
        "",
        f"Source: `{Path(r.source).relative_to(ROOT)}` ({r.day})",
        "",
        f"- judges: {len(show.judges)}, rings: {len(show.rings)}, blocks: {len(prog.blocks)}, events: {len(show.events)}",
        f"- judging starts: {fmt_time(show.start) if show.start else '?'}",
    ]
    if plan:
        lines.append(
            f"- groups: {fmt_time(plan.start) if plan.start else '?'} in rings "
            f"{', '.join(str(x.number) for x in plan.rings)}; order: {', '.join(g.value for g in plan.order)}; "
            f"BIS: {plan.bis_judge.name if plan.bis_judge else '?'}"
        )
    lines += ["", "## Checks", "", "| check | stated | parsed | |", "|---|---|---|---|"]
    for what, stated, parsed in r.checks:
        lines.append(f"| {what} | {stated} | {parsed} | {'ok' if stated == parsed else '**MISMATCH**'} |")
    if dropped:
        lines += ["", "## Not kept (only regular breed events are)", ""]
        lines += [f"- {kind} ({comp}): {n}" for (kind, comp), n in sorted(dropped.items())]
    for title, items in (
        ("Breed index mismatches", r.index_mismatches),
        ("Unknown breeds", r.unknown_breeds),
        ("Unparsed lines", r.unparsed),
        ("Notes", r.notes),
    ):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {x}" for x in items]
    path.write_text("\n".join(lines) + "\n")


def build_bradshaw() -> list[tuple[str, ParsedDay]]:
    parser = BradshawParser()
    src = RAW / "bradshaw"
    with open(src / "manifest.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["include"] == "1"]
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    out = []
    for fname, entries in by_file.items():
        days = {pd.report.day: pd for pd in parser.parse(src / fname)}
        for e in entries:
            pd = days.get(e["day"])
            if pd is None:
                raise SystemExit(f"{fname}: no all-breed show parsed for {e['day']}")
            pd.program.show.name = e["show"]
            pd.program.show.date = e["date"]
            if e["notes"]:
                pd.report.notes.append(f"manifest: {e['notes']}")
            key = f"{e['date']}-{slug(e['show'])}"
            d = PARSED / "bradshaw" / key
            program, dropped = regular_breed_only(pd.program)
            write_program(program, d, source=f"bradshaw/{fname}#{e['day']}")
            write_report(pd, d / "report.md", dropped)
            out.append((key, pd))
    return out


def main() -> None:
    results = build_bradshaw()
    print(f"{'show':55} {'checks':>9} {'index':>9}")
    for key, pd in results:
        r = pd.report
        idx = next((c for c in r.checks if c[0].startswith("breed index")), None)
        ok = len(r.checks) - len(r.failed_checks)
        print(f"{key:55} {ok:>4}/{len(r.checks):<4} {(f'{idx[2]}/{idx[1]}' if idx else '-'):>9}")


if __name__ == "__main__":
    main()
