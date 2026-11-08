"""A greedy baseline scheduler: simple, transparent rules, no search.

It exists so that the optimizer has something to beat, and so we can see which constraints
make schedules hard. Steps:

1. **Target group order.** The order that achieves the judge-chain lower bound
   (``bounds.finish_lower_bound``). It is used only to set priorities; the final group
   order is chosen in step 5.
2. **Each judge's running order.** A judge's all-breed events are sorted by target group
   order, so breeds of early groups are judged first (H7, S4). Within a group, ground,
   ramp and table breeds are kept together (S3). Sweepstakes come just before their breed.
   Miscellaneous breeds and Junior Showmanship go last, since no group waits for them.
3. **Blocks.** Pack the running order into consecutive blocks of at most
   ``max_block_minutes`` (about an hour, Rules Ch.7 §12). An event too big for a block
   is a block of its own.
4. **Rings and times.** Each judge works in one ring all day, large enough for all of their
   breeds (H16) if any ring is, so there are no ring changes
   (S2). Judges are placed most urgent first, where urgency is how soon their work is
   needed by the target group schedule. Each judge takes the ring where they would finish
   earliest. A group ring is used only by a judge who will be done before the target
   group start (H9), unless no ring allows that. Blocks start on the time grid, as soon
   as the judge and the ring are both free of earlier blocks and pinned commitments.
   A judge with over five hours of judging takes a minimum lunch at the first block
   boundary inside the lunch window, or waits for the window to open if the next block
   would run past its end (H6).
5. **Groups.** Order the groups by when each is ready: its breeds are done and its judge
   is free (H7, H8). With one ring and fixed ready times, earliest-ready-first gives the
   earliest finish. The first group is published once it is ready, every group ring
   is clear (H9), the breeds of at least two groups are done (H13), and every judge is
   back from lunch (H14).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from optdog.domain import (
    VARIETY_GROUPS,
    Block,
    Break,
    EventKind,
    Group,
    GroupsPlan,
    JudgingEvent,
    JudgingProgram,
    Judge,
    Ring,
    ScheduledBlock,
    Show,
)
from optdog.params import ShowParameters
from optdog.solve.bounds import FinishBound, finish_lower_bound


@dataclass(frozen=True)
class GreedyOptions:
    max_block_minutes: float = 65.0  # one hour plus a 5-minute slot: humans' p90 block is 27 dogs ≈ 65 min (E)
    group_order: tuple[Group, ...] | None = None  # target order; default: the lower bound's order


# --------------------------------------------------------------------------- calendars


class Calendar:
    """Busy intervals of one ring or one judge."""

    def __init__(self) -> None:
        self.busy: list[tuple[float, float]] = []

    def add(self, start: float, end: float) -> None:
        self.busy.append((start, end))
        self.busy.sort()

    def clashes(self, start: float, end: float) -> tuple[float, float] | None:
        for a, b in self.busy:
            if a < end and start < b:
                return a, b
        return None


def _ceil(t: float, slot: int) -> int:
    return int(math.ceil(t / slot - 1e-9) * slot)


def _fit(t: float, duration: float, slot: int, *cals: Calendar) -> int:
    """Earliest grid time ≥ t at which [s, s + duration) is free in every calendar."""
    s = _ceil(t, slot)
    while True:
        for cal in cals:
            clash = cal.clashes(s, s + duration)
            if clash:
                s = _ceil(clash[1], slot)
                break
        else:
            return s


# --------------------------------------------------------------------------- running order and blocks


_EXAM_ORDER = {"ground": 0, "ramp": 1, "table": 2}


def _exam(e: JudgingEvent) -> str:
    if e.breed is None:
        return "ground"
    ex = e.breed.examination
    return "table" if ex.needs_table else "ramp" if ex.needs_ramp else "ground"


def running_order(events: list[JudgingEvent], rank: dict[Group, int]) -> list[JudgingEvent]:
    def key(e: JudgingEvent):
        g = e.breed.group if e.breed else None
        if e.kind is EventKind.JUNIOR_SHOWMANSHIP:
            r = len(rank) + 2
        elif g is None or not g.is_variety_group:
            r = len(rank) + 1  # Miscellaneous, FSS: nothing waits for them
        else:
            r = rank[g]
        breed = e.breed.name if e.breed else e.name
        return r, _EXAM_ORDER[_exam(e)], breed, 0 if e.kind is EventKind.SWEEPSTAKES else 1, e.name

    return sorted(events, key=key)


def pack_blocks(judge: Judge, order: list[JudgingEvent], max_minutes: float) -> list[Block]:
    blocks: list[list[JudgingEvent]] = []
    for e in order:
        if blocks and sum(x.duration for x in blocks[-1]) + e.duration <= max_minutes:
            blocks[-1].append(e)
        else:
            blocks.append([e])
    return [Block(judge, tuple(b)) for b in blocks]


# --------------------------------------------------------------------------- placement


@dataclass
class _Placement:
    ring: Ring
    blocks: list[ScheduledBlock]
    lunch: Break | None

    @property
    def end(self) -> float:
        return max(sb.end for sb in self.blocks)


_RULE = -1  # lunch at the first block boundary inside the lunch window


def _place_chain(judge: Judge, blocks: list[Block], ring: Ring, ring_cal: Calendar, judge_cal: Calendar,
                 show: Show, p: ShowParameters, lunch_before: int | None = _RULE) -> _Placement:
    """Place the judge's blocks in order in ``ring``, each as early as possible.

    ``lunch_before`` is the index of the block lunch comes before, None for no lunch, or
    ``_RULE`` to take it at the first boundary inside the lunch window.
    """
    l0, l1 = p.lunch_window
    t: float = show.start
    out: list[ScheduledBlock] = []
    lunch = None
    for i, b in enumerate(blocks):
        d = b.duration
        if lunch_before == _RULE:
            take = lunch is None and out and (t >= l0 or t + d > l1)
        else:
            take = i == lunch_before
        if take:
            # take lunch at this block boundary, waiting for the window to open if need be (H6)
            ls = _ceil(max(t, l0), p.slot_minutes)
            lunch = Break(judge, ring, ls, p.lunch_min_minutes)
            t = ls + p.lunch_min_minutes
        s = _fit(t, d, p.slot_minutes, ring_cal, judge_cal)
        out.append(ScheduledBlock(b, ring, s))
        t = s + d
    return _Placement(ring, out, lunch)


def _lunch_gap(judge: Judge, placement: _Placement, show: Show, p: ShowParameters) -> Break | None:
    """A gap of lunch length in the judge's whole day (pinned blocks included), as a Break."""
    items = sorted([(sb.start, sb.end, sb.ring) for sb in placement.blocks]
                   + [(sb.start, sb.end, sb.ring) for sb in show.pinned if sb.judge == judge])
    for (_, a_end, ring), (b_start, _, _) in zip(items, items[1:]):
        start = _ceil(max(a_end, p.lunch_window[0]), p.slot_minutes)  # the lunch must start in the window (H6)
        if start <= p.lunch_window[1] and b_start - start >= p.lunch_min_minutes:
            return Break(judge, ring, start, p.lunch_min_minutes)
    return None


def _place_judge(judge: Judge, blocks: list[Block], ring: Ring, ring_cal: Calendar, judge_cal: Calendar,
                 show: Show, p: ShowParameters) -> _Placement:
    """Place the chain; if the day needs lunch (H6), make sure it has a lunch-length gap."""
    work = sum(e.duration for e in show.events_for(judge))  # pinned blocks count toward the day
    if work <= p.lunch_required_after_minutes:
        return _place_chain(judge, blocks, ring, ring_cal, judge_cal, show, p, None)
    pl = _place_chain(judge, blocks, ring, ring_cal, judge_cal, show, p, _RULE)
    if pl.lunch is not None and pl.lunch.start > p.lunch_window[1]:
        pl.lunch = None  # the boundary came too late (a pinned block pushed it): look for another
    if pl.lunch is None:
        pl.lunch = _lunch_gap(judge, pl, show, p)
    if pl.lunch is not None:
        return pl
    # The rule found no boundary (e.g. a pinned block cuts the day): try every boundary
    # and keep the one that finishes earliest.
    tries = []
    for k in range(1, len(blocks) + 1):
        if k == len(blocks):  # lunch after the last block, before a later pinned block
            alt = _place_chain(judge, blocks, ring, ring_cal, judge_cal, show, p, None)
        else:
            alt = _place_chain(judge, blocks, ring, ring_cal, judge_cal, show, p, k)
            alt.lunch = None  # count only real gaps
        alt.lunch = _lunch_gap(judge, alt, show, p)
        if alt.lunch is not None:
            tries.append(alt)
    return min(tries, key=lambda x: x.end) if tries else pl


def _urgency(judge: Judge, order: list[JudgingEvent], show: Show, bound: FinishBound, p: ShowParameters) -> float:
    """Latest time the judge could start and still meet the relaxed group schedule (smaller = more urgent)."""
    latest = math.inf
    done = 0.0
    last_of: dict[Group, float] = {}
    for e in order:
        done += e.duration
        if e.feeds_group is not None:
            last_of[e.feeds_group] = done
    for g, w in last_of.items():
        latest = min(latest, bound.group_starts.get(g, math.inf) - w)
    for g, gj in show.group_judges.items():
        if gj == judge:
            latest = min(latest, bound.group_starts.get(g, math.inf) - (bound.judge_free[judge] - show.start))
    return latest


def greedy_program(show: Show, params: ShowParameters | None = None, options: GreedyOptions | None = None) -> JudgingProgram:
    p = (params or ShowParameters()).for_show(show)
    o = options or GreedyOptions()
    bound = finish_lower_bound(show, p)
    target = o.group_order or bound.order
    rank = {g: i for i, g in enumerate(target)}
    groups_target = min(bound.group_starts.values(), default=show.start)

    ring_cal = {r: Calendar() for r in show.rings}
    judge_cal = {j: Calendar() for j in show.judges}
    for sb in show.pinned:
        ring_cal.setdefault(sb.ring, Calendar()).add(sb.start, sb.end)
        judge_cal.setdefault(sb.judge, Calendar()).add(sb.start - p.judge_travel_minutes, sb.end + p.judge_travel_minutes)

    unpinned = show.unpinned_events()
    plans = []
    for j in show.judges:
        evs = [e for e in unpinned if e.judge == j]
        if not evs:
            continue
        order = running_order(evs, rank)
        work = sum(e.duration for e in evs)
        plans.append((_urgency(j, order, show, bound, p), -work, j.name, j, pack_blocks(j, order, o.max_block_minutes)))
    plans.sort(key=lambda x: x[:3])

    group_rings = set(show.group_rings)
    blocks: list[ScheduledBlock] = list(show.pinned)
    breaks: list[Break] = []
    for _, _, _, judge, jblocks in plans:
        need = max(b.min_ring for b in jblocks)  # H16: one ring all day, so it must fit every block
        fits = [r for r in show.rings if r.size >= need] or [r for r in show.rings if r.size == max(x.size for x in show.rings)]
        options_ = [_place_judge(judge, jblocks, r, ring_cal[r], judge_cal[judge], show, p) for r in fits]
        best = min(options_, key=lambda pl: (pl.ring in group_rings and pl.end > groups_target, pl.end, pl.ring.number))
        for sb in best.blocks:
            ring_cal[best.ring].add(sb.start, sb.end)
            judge_cal[judge].add(sb.start, sb.end)
        if best.lunch:
            judge_cal[judge].add(best.lunch.start, best.lunch.end)
            breaks.append(best.lunch)
        blocks += best.blocks

    program = JudgingProgram(show, blocks, breaks, None)
    program.groups = plan_groups(program, p)
    return program


def plan_groups(program: JudgingProgram, p: ShowParameters) -> GroupsPlan:
    """Earliest-ready-first group order, published once the first is ready and the group rings are clear."""
    show = program.show
    provisional = GroupsPlan(show.group_rings, show.start, VARIETY_GROUPS, dict(show.group_judges), show.bis_judge)
    program.groups = provisional
    timeline = program.group_timeline(judge_travel_minutes=p.judge_travel_minutes)
    ready = {s.group: s.ready for s in timeline.groups}
    order = tuple(sorted(VARIETY_GROUPS, key=lambda g: (ready[g], g.value)))
    rings_free = max((sb.end for sb in program.blocks if sb.ring in set(show.group_rings)), default=show.start)
    done = sorted(s.breeds_done for s in timeline.groups if show.group_entrants(s.group) > 0)
    need = min(p.groups_complete_before_start, len(done))
    enough_groups_done = done[need - 1] if need else show.start  # H13
    lunches_over = show.start
    if p.groups_after_all_lunches:  # H14
        from optdog.validate import lunch_ends

        lunches_over = max(lunch_ends(program, p).values(), default=show.start)
    start = _ceil(max(ready[order[0]], rings_free + p.group_ring_setup_minutes, enough_groups_done, lunches_over),
                  p.slot_minutes)
    return GroupsPlan(show.group_rings, start, order, dict(show.group_judges), show.bis_judge)
