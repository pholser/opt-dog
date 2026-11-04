"""Check a JudgingProgram against the hard constraints and score the soft costs (SPEC §4–§6).

The validator works on any JudgingProgram — a parsed human-made program or a solver
result — and never repairs it. Times are *planned* times: each block starts at its
published time and lasts entries × rate.

Hard constraints checked here:
    H1  blocks of about one hour (single breed > 30 entries exempt)
    H3  no overlap within a ring
    H4  a judge is never in two places at once (+ travel margin between rings)
    H6  judges with > 5 hours of judging get a 45–60 minute break
    H7  the first group is not scheduled before its breeds are done
    H8  group judges are free (breed assignments done) before their group
    H9  ALL group rings are free of breed judging before the first group begins
    H12 judging ends by the posted closing time (if known)
    V1  judge daily entry limit (input check)
Not checkable from a program alone: H5 (published order is taken as given),
H10 (sequencing is built into the group timeline), H11 (pinned events are not
recorded in parsed programs). S1 (exhibitor conflicts) needs exhibitor data.

Soft costs scored: S2 ring changes, S3 equipment switches, S4 group switches,
S5 judge idle, S8 dogs over the final-segment limit (AKC's 50-dog final-segment
rule is a soft cost by decision: real programs exceed it).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from optdog.domain import (
    Competition,
    EventKind,
    GroupTimeline,
    JudgingProgram,
    Judge,
    ScheduledBlock,
    fmt_time,
)
from optdog.params import ShowParameters

CONFORMATION = {Competition.ALL_BREED, Competition.SPECIALTY, Competition.BEGINNER_PUPPY, Competition.FSS_OPEN}


@dataclass(frozen=True)
class Violation:
    code: str  # H1, H2, ... V1
    subject: str  # e.g. "Ring 4 8:00 AM (Mr. X)" or a judge name
    message: str
    amount: float  # minutes or dogs over the limit

    def __str__(self) -> str:
        return f"{self.code} {self.subject}: {self.message}"


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)
    soft: dict[str, float] = field(default_factory=dict)  # S2..S5 raw quantities
    metrics: dict[str, float | int | str | None] = field(default_factory=dict)
    timeline: GroupTimeline | None = None
    objective: float | None = None

    def by_code(self) -> dict[str, list[Violation]]:
        out: dict[str, list[Violation]] = defaultdict(list)
        for v in self.violations:
            out[v.code].append(v)
        return dict(out)

    @property
    def feasible(self) -> bool:
        return not self.violations


def _label(sb: ScheduledBlock) -> str:
    return f"{sb.ring} {fmt_time(sb.start)} ({sb.judge.name})"


def _conformation_blocks(program: JudgingProgram) -> list[ScheduledBlock]:
    return [sb for sb in program.blocks if sb.block.events[0].competition in CONFORMATION]


def validate(program: JudgingProgram, params: ShowParameters | None = None) -> ValidationResult:
    p = params or ShowParameters()
    res = ValidationResult()
    blocks = _conformation_blocks(program)
    by_ring: dict = defaultdict(list)
    by_judge: dict[Judge, list[ScheduledBlock]] = defaultdict(list)
    for sb in blocks:
        by_ring[sb.ring].append(sb)
        by_judge[sb.judge].append(sb)
    for lst in list(by_ring.values()) + list(by_judge.values()):
        lst.sort(key=lambda b: (b.start, b.end))

    # ---------------------------------------------------------------- H1 block size; S8 final segment
    final_excess = 0
    for ring, seq in by_ring.items():
        for i, sb in enumerate(seq):
            b = sb.block
            breed_events = [e for e in b.events if e.kind is EventKind.BREED]
            single = len(b.events) == 1
            if single and b.entries > p.single_breed_exception_entries:
                continue  # Rules Ch.7 §12 exception
            if i == len(seq) - 1:  # final segment of the ring schedule: may expand (soft cap, S8)
                if b.entries > p.final_segment_max_dogs and not single:
                    final_excess += b.entries - p.final_segment_max_dogs
                continue
            limit = p.block_target_minutes + p.block_tolerance_minutes
            if b.duration > limit:
                res.violations.append(
                    Violation("H1", _label(sb), f"block is {b.duration:.0f} min ({b.entries} dogs); limit {limit:.0f}",
                              b.duration - p.block_target_minutes)
                )

    # ---------------------------------------------------------------- H3 ring overlap
    for ring, seq in by_ring.items():
        for a, b in zip(seq, seq[1:]):
            over = a.end - b.start
            if over > p.overlap_tolerance_minutes:
                res.violations.append(
                    Violation("H3", _label(b), f"starts {over:.0f} min before previous block in ring ends ({_label(a)})", over)
                )

    # ---------------------------------------------------------------- H4 judge overlap / travel / lunch
    breaks_by_judge: dict[Judge, list] = defaultdict(list)
    for br in program.breaks:
        if br.minutes is not None:
            breaks_by_judge[br.judge].append(br)
    for judge, seq in by_judge.items():
        for a, b in zip(seq, seq[1:]):
            need = p.judge_travel_minutes if a.ring != b.ring else 0.0
            short = (a.end + need) - b.start
            if short > p.overlap_tolerance_minutes:
                what = f"needs {need:.0f} min to move from {a.ring}" if need else "overlaps previous block"
                res.violations.append(Violation("H4", _label(b), f"{what}; short by {short:.0f} min", short))
        for br in breaks_by_judge.get(judge, []):
            for sb in seq:
                over = min(sb.end, br.end) - max(sb.start, br.start)
                if over > p.overlap_tolerance_minutes:
                    res.violations.append(
                        Violation("H4", _label(sb), f"runs {over:.0f} min into lunch at {fmt_time(br.start)}", over)
                    )

    # ---------------------------------------------------------------- H6 lunch; S2 ring changes; S5 idle
    lunch_gaps: dict[Judge, tuple[float, float] | None] = {}
    ring_changes = ring_changes_at_lunch = 0
    idle = 0.0
    lunch_outside_window = 0
    for judge, seq in by_judge.items():
        work = sum(sb.block.duration for sb in seq)
        gaps = [(a.end, b.start - a.end, a, b) for a, b in zip(seq, seq[1:]) if b.start - a.end > 0]
        # A published break counts as lunch (the block before it may overrun a little; that is
        # reported under H4). Otherwise any planned gap of lunch length counts.
        published = [
            (br.start, br.minutes, a, b)
            for br in breaks_by_judge.get(judge, [])
            if br.minutes >= p.lunch_min_minutes
            for a, b in zip(seq, seq[1:])
            if a.start <= br.start < b.start
        ]
        long_gaps = published or [g for g in gaps if g[1] >= p.lunch_min_minutes]
        in_window = [g for g in long_gaps if p.lunch_window[0] <= g[0] <= p.lunch_window[1]]
        lunch = max(in_window or long_gaps, key=lambda g: g[1], default=None)
        lunch_gaps[judge] = (lunch[0], lunch[1]) if lunch else None
        if work > p.lunch_required_after_minutes:
            if lunch is None:
                res.violations.append(
                    Violation("H6", judge.name,
                              f"{work:.0f} min of judging with no break of {p.lunch_min_minutes}+ min", work - p.lunch_required_after_minutes)
                )
            elif lunch not in in_window:
                lunch_outside_window += 1
        for a, b in zip(seq, seq[1:]):
            if a.ring != b.ring:
                if lunch is not None and lunch[2] is a and lunch[3] is b:
                    ring_changes_at_lunch += 1
                else:
                    ring_changes += 1
        idle += sum(g[1] for g in gaps if lunch is None or g[2] is not lunch[2])

    # ---------------------------------------------------------------- S3 equipment switches per ring
    equipment_switches = 0
    for ring, seq in by_ring.items():
        kinds = []
        for sb in seq:
            for e in sb.block.events:
                if e.kind is EventKind.BREED and e.breed is not None:
                    ex = e.breed.examination
                    kinds.append("table" if ex.needs_table else "ramp" if ex.needs_ramp else "ground")
        equipment_switches += sum(1 for x, y in zip(kinds, kinds[1:]) if x != y)

    # ---------------------------------------------------------------- S4 group switches per judge
    extra_group_switches = 0
    for judge, seq in by_judge.items():
        groups = [e.group for sb in seq for e in sb.block.events if e.kind is EventKind.BREED and e.group is not None]
        switches = sum(1 for x, y in zip(groups, groups[1:]) if x != y)
        extra_group_switches += max(0, switches - (len(set(groups)) - 1))

    # ---------------------------------------------------------------- groups: H7, H8, H9, H12
    timeline = program.group_timeline(judge_travel_minutes=p.judge_travel_minutes)
    res.timeline = timeline
    plan = program.groups
    if timeline is not None and plan is not None:
        first = timeline.groups[0]
        if first.breeds_done > plan.start + p.overlap_tolerance_minutes:
            res.violations.append(
                Violation("H7", first.group.value,
                          f"first group published {fmt_time(plan.start)} but its breeds finish {fmt_time(int(first.breeds_done))}",
                          first.breeds_done - plan.start)
            )
        if first.ready > first.breeds_done and first.ready > plan.start + p.overlap_tolerance_minutes:
            res.violations.append(
                Violation("H8", first.group.value,
                          f"group judge busy until {fmt_time(int(first.ready))}; group published {fmt_time(plan.start)}",
                          first.ready - plan.start)
            )
        # every group ring must be clear before the first group (U)
        group_ring_free = max((sb.end for r in plan.rings for sb in by_ring.get(r, [])), default=None)
        if group_ring_free is not None:
            late = group_ring_free + p.group_ring_setup_minutes - plan.start
            if late > p.overlap_tolerance_minutes:
                res.violations.append(
                    Violation("H9", ", ".join(str(r) for r in plan.rings),
                              f"breed judging in group ring(s) runs to {fmt_time(int(group_ring_free))}; groups at {fmt_time(plan.start)}",
                              late)
                )
        res.metrics["group_ring_free"] = fmt_time(int(group_ring_free)) if group_ring_free else None
        res.metrics["group_ring_slack_min"] = (plan.start - group_ring_free) if group_ring_free else None
        res.metrics["groups_start"] = fmt_time(plan.start)
        res.metrics["group_wait_min"] = round(sum(s.wait for s in timeline.groups), 1)
        res.metrics["finish"] = fmt_time(int(timeline.finish))
        if p.closing_time is not None and timeline.finish > p.closing_time:
            res.violations.append(Violation("H12", "show", f"finishes {fmt_time(int(timeline.finish))}", timeline.finish - p.closing_time))

    # ---------------------------------------------------------------- V1 daily limit
    show = program.show
    for judge in show.judges:
        load = show.judge_load(judge)
        evs = show.events_for(judge)
        specialty_only = evs and all(e.competition is Competition.SPECIALTY for e in evs)
        limit = p.specialty_only_daily_limit if specialty_only else p.judge_daily_limit
        if load > limit:
            res.violations.append(Violation("V1", judge.name, f"{load} entries (limit {limit})", load - limit))

    # ---------------------------------------------------------------- soft costs, metrics, objective
    off_grid = sum(1 for sb in blocks if sb.start % p.slot_minutes)
    res.soft = {
        "S2_ring_changes": ring_changes,
        "S2_ring_changes_at_lunch": ring_changes_at_lunch,
        "S3_equipment_switches": equipment_switches,
        "S4_extra_group_switches": extra_group_switches,
        "S5_judge_idle_min": round(idle, 1),
        "S8_final_segment_excess_dogs": final_excess,
    }
    res.metrics.update(
        {
            "blocks": len(blocks),
            "judges": len(by_judge),
            "rings": len(by_ring),
            "judging_start": fmt_time(min(sb.start for sb in blocks)) if blocks else None,
            "judges_lunch_outside_window": lunch_outside_window,
            "blocks_off_grid": off_grid,
        }
    )
    if timeline is not None and blocks:
        w = p.weights
        t0 = min(sb.start for sb in blocks)
        res.objective = (
            w.finish * (timeline.finish - t0)
            + w.ring_change * ring_changes
            + w.ring_change_at_lunch * ring_changes_at_lunch
            + w.equipment_switch * equipment_switches
            + w.group_switch * extra_group_switches
            + w.judge_idle * idle
            + w.final_segment_excess * final_excess
        )
    return res
