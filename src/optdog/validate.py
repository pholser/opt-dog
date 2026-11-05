"""Check a JudgingProgram against the hard constraints and score the soft costs (SPEC §4–§6).

The validator works on any JudgingProgram — a parsed human-made program or a solver
result — and never repairs it. Times are *planned* times: each block starts at its
published time and lasts entries × rate.

Hard constraints checked here:
    H1  blocks of about one hour (single breed > 30 entries exempt)
    H3  no overlap within a ring
    H4  a judge is never in two places at once (+ travel margin between rings)
    H6  judges with > 5 hours of judging get a 45–60 minute break; every lunch starts in the lunch window
    H7  the first group is not scheduled before its breeds are done
    H8  group judges are free (breed assignments done) before their group
    H9  ALL group rings are free of breed judging before the first group begins
    H12 judging ends by the posted closing time (if known)
    H13 breeds of at least two groups are done before the first group begins
    H14 the first group begins only after every judge's lunch has ended
    V1  judge daily entry limit (input check)
Not checkable from a program alone: H5 (published order is taken as given),
H10 (sequencing is built into the group timeline), H11 (pinned events are not
recorded in parsed programs). S1 (exhibitor conflicts) is scored when the show
has entries (``Show.entries``; synthetic for now).

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


def lunch_fits_window(start: float, length: float, params: ShowParameters, published: bool) -> bool:
    """Can this lunch start inside the lunch window (H6)?

    A published break starts at its published time. A gap in a judge's day can hold a
    minimum-length lunch starting anywhere in it, so it fits if some start in the window
    leaves room for the lunch.
    """
    l0, l1 = params.lunch_window
    if published:
        return l0 <= start <= l1
    return max(start, l0) <= min(l1, start + length - params.lunch_min_minutes)


def lunch_ends(program: JudgingProgram, params: ShowParameters) -> dict[Judge, float]:
    """When each conformation judge's lunch ends.

    A published break counts whatever the judge's load, if the judge judges again after it. A judge with no published break
    but more than five hours of judging takes lunch in their longest lunch-length gap
    (preferring one that starts in the lunch window), for the minimum lunch length.
    """
    p = params.for_show(program.show)
    blocks = _conformation_blocks(program)
    judges = {sb.judge for sb in blocks}
    out: dict[Judge, float] = {}
    last_start = {}
    for sb in blocks:
        last_start[sb.judge] = max(last_start.get(sb.judge, sb.start), sb.start)
    for br in program.breaks:
        if br.minutes and br.judge in judges and br.start < last_start[br.judge]:  # a break within the assignment
            out[br.judge] = max(out.get(br.judge, 0.0), br.start + br.minutes)
    for judge in judges - out.keys():
        seq = sorted((sb for sb in blocks if sb.judge == judge), key=lambda b: b.start)
        if sum(sb.block.duration for sb in seq) <= p.lunch_required_after_minutes:
            continue
        gaps = [(a.end, b.start - a.end) for a, b in zip(seq, seq[1:]) if b.start - a.end >= p.lunch_min_minutes]
        in_window = [g for g in gaps if lunch_fits_window(g[0], g[1], p, published=False)]
        best = max(in_window or gaps, key=lambda g: g[1], default=None)
        if best:
            start = max(best[0], p.lunch_window[0]) if best in in_window else best[0]
            out[judge] = start + p.lunch_min_minutes
    return out


def validate(program: JudgingProgram, params: ShowParameters | None = None) -> ValidationResult:
    p = (params or ShowParameters()).for_show(program.show)
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
    pinned = {id(sb) for sb in program.show.pinned}
    for ring, seq in by_ring.items():
        for i, sb in enumerate(seq):
            b = sb.block
            if id(sb) in pinned:
                continue  # a published commitment of the instance (H11), not a decision being checked
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
        in_window = [g for g in long_gaps if lunch_fits_window(g[0], g[1], p, published=bool(published))]
        lunch = max(in_window or long_gaps, key=lambda g: g[1], default=None)
        lunch_gaps[judge] = (lunch[0], lunch[1]) if lunch else None
        if work > p.lunch_required_after_minutes:
            if lunch is None:
                res.violations.append(
                    Violation("H6", judge.name,
                              f"{work:.0f} min of judging with no break of {p.lunch_min_minutes}+ min", work - p.lunch_required_after_minutes)
                )
            elif lunch not in in_window and not published:  # published breaks are checked below
                res.violations.append(
                    Violation("H6", judge.name, f"no lunch-length gap that can start in the lunch window "
                              f"{fmt_time(p.lunch_window[0])}–{fmt_time(p.lunch_window[1])}", 0.0)
                )
        for a, b in zip(seq, seq[1:]):
            if a.ring != b.ring:
                if lunch is not None and lunch[2] is a and lunch[3] is b:
                    ring_changes_at_lunch += 1
                else:
                    ring_changes += 1
        idle += sum(g[1] for g in gaps if lunch is None or g[2] is not lunch[2])

    # every lunch break within a judge's assignment starts inside the window (H6, U); a break
    # after the judge's last block is free time, not a break in judging
    conf_judges = set(by_judge)
    l0, l1 = p.lunch_window
    for br in program.breaks:
        within = br.judge in conf_judges and any(sb.start > br.start for sb in by_judge[br.judge])
        if within and not lunch_fits_window(br.start, br.minutes or 0, p, published=True):
            res.violations.append(
                Violation("H6", br.judge.name, f"lunch at {fmt_time(br.start)} is outside the lunch window "
                          f"{fmt_time(l0)}–{fmt_time(l1)}", min(abs(br.start - l0), abs(br.start - l1)))
            )

    # ---------------------------------------------------------------- S3 equipment switches per ring
    # consecutive breeds in a ring with different equipment; free across a break, i.e. when the
    # ring is idle at least `equipment_free_gap_minutes` minutes between them (U)
    equipment_switches = 0
    for ring, seq in by_ring.items():
        last_kind, idle, t = None, 0.0, None
        for sb in seq:
            for e, off in sb.block.offsets():
                s = sb.start + off
                if t is not None:
                    idle += max(0.0, s - t)
                t = max(t or s, s + e.duration)
                k = e.equipment
                if k is None:
                    continue
                if last_kind is not None and k != last_kind and idle < p.equipment_free_gap_minutes:
                    equipment_switches += 1
                last_kind, idle = k, 0.0

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
        # H13: breeds of at least N groups complete before the first group (Show Manual §6)
        entered = [s for s in timeline.groups if program.show.group_entrants(s.group) > 0]
        need = min(p.groups_complete_before_start, len(entered))
        done = sorted(s.breeds_done for s in entered)
        if need and done[need - 1] > plan.start + p.overlap_tolerance_minutes:
            res.violations.append(
                Violation("H13", "groups",
                          f"only {sum(1 for x in done if x <= plan.start + p.overlap_tolerance_minutes)} group(s) have all "
                          f"breeds done by {fmt_time(plan.start)}; {need} needed (done at {fmt_time(int(done[need - 1]))})",
                          done[need - 1] - plan.start)
            )
        # H14: no group before every judge's lunch is over (E: all 11 parsed days)
        lunch_end = lunch_ends(program, p)
        if p.groups_after_all_lunches and lunch_end:
            judge, end = max(lunch_end.items(), key=lambda kv: kv[1])
            if end > plan.start + p.overlap_tolerance_minutes:
                res.violations.append(
                    Violation("H14", judge.name, f"at lunch until {fmt_time(int(end))}; groups at {fmt_time(plan.start)}",
                              end - plan.start)
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

    # ---------------------------------------------------------------- S1 exhibitor conflicts
    conflict_minutes = None
    if program.show.entries:
        from optdog.conflicts import exhibitor_conflicts

        cr = exhibitor_conflicts(program, p.exhibitor_travel_minutes)
        conflict_minutes = cr.minutes
        res.metrics["conflict_pairs"] = len(cr.conflicts)
        res.metrics["exhibitors_in_conflict"] = len(cr.by_exhibitor())
        res.metrics["entries_unscheduled"] = len(cr.unscheduled)

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
    if conflict_minutes is not None:
        res.soft["S1_conflict_min"] = round(conflict_minutes, 1)
    res.metrics.update(
        {
            "blocks": len(blocks),
            "judges": len(by_judge),
            "rings": len(by_ring),
            "judging_start": fmt_time(min(sb.start for sb in blocks)) if blocks else None,
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
            + w.conflict * (conflict_minutes or 0.0)
        )
    return res
