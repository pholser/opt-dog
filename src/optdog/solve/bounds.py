"""Lower bounds on show finish (BIS end), to judge how good any schedule is.

Every bound relaxes the problem: if it ignores a constraint, any real schedule finishes at
or after it. Two bounds, combined by ``max``:

**Judge-chain bound.** Ignore rings and keep only the judges. Before group *g* starts,
every judge must have finished their breeds in *g* (H7) and in every group judged before
*g*, since those groups started even earlier. A group judge must have finished all their
judging, plus lunch if their day needs one (H6, H8); a lunch starts no earlier than the
lunch window opens and is followed by at least one event. If any judge needs lunch, no group
starts before that lunch can have ended (H14). That gives each group a release time,
which depends only on the *set* of groups judged before it. Groups run one after another
(H10), so a dynamic program over the 2⁷ subsets of groups finds the order with the
earliest finish under this relaxation. That order is also a good target group order for
a heuristic. No group starts before the breeds of at least two groups (any two) are done (H13).
The earliest that can happen is the smallest, over pairs of groups, of the time their busiest
judge needs to finish both.

**Ring-capacity bound.** Every breed that feeds a group must be judged before the last
group starts. Until the first group, all |R| rings can judge breeds. After it, the group
ring(s) are taken (H9), so only |R| − |RG| can. Each group takes ``D_g`` regardless of
order, so the groups need at least Σ D_g between the first group's start and the end of
the last group.

See also `relaxation.py`, a CP-SAT relaxation that couples these and is much tighter.

What the bounds ignore: the show's closing time (an upper limit, so the bounds stay valid), the lunch window's closing time, the time grid, travel margins, pinned blocks (except as a judge's
commitments), equipment and every soft cost. They are loose wherever those bind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from optdog.domain import VARIETY_GROUPS, Duration, Group, Judge, Show, TimeOfDay
from optdog.params import ShowParameters


@dataclass(frozen=True)
class FinishBound:
    chain: TimeOfDay  # judge-chain bound on BIS end
    capacity: TimeOfDay  # ring-capacity bound on BIS end
    order: tuple[Group, ...]  # group order achieving the chain bound
    group_starts: dict[Group, TimeOfDay] = field(default_factory=dict, compare=False)  # in the relaxed schedule
    judge_free: dict[Judge, TimeOfDay] = field(default_factory=dict, compare=False)  # earliest each judge can be done

    @property
    def value(self) -> TimeOfDay:
        return max(self.chain, self.capacity)


def judge_work(show: Show, judge: Judge) -> Duration:
    """Planned time of conformation judging (pinned blocks included)."""
    return Duration.total(e.duration for e in show.events_for(judge))


def earliest_free(show: Show, params: ShowParameters) -> dict[Judge, TimeOfDay]:
    """Earliest time each judge could finish all their judging: back-to-back from the start,
    plus a minimum lunch if the day exceeds five hours, and not before any pinned block ends."""
    params = params.for_show(show)
    t0 = show.start
    out = {}
    for j in show.judges:
        work = judge_work(show, j)
        needs_lunch = work > params.lunch_required_after
        lunch = params.lunch_min if needs_lunch else Duration.ZERO
        pinned_end = max((sb.end for sb in show.pinned if sb.judge == j), default=t0)
        out[j] = max(t0 + work + lunch, pinned_end)
        if needs_lunch:
            # lunch starts no earlier than the window opens (H6), and at least one event follows it
            shortest = min(e.duration for e in show.events_for(j))
            out[j] = max(out[j], params.lunch_window[0] + params.lunch_min + shortest)
    return out


def finish_lower_bound(show: Show, params: ShowParameters | None = None) -> FinishBound:
    p = (params or ShowParameters()).for_show(show)
    t0 = show.start
    groups = list(VARIETY_GROUPS)
    dur = {g: show.group_duration(g) for g in groups}
    free = earliest_free(show, p)

    # feeding work per (judge, group)
    feed: dict[Judge, list[Duration]] = {}
    for e in show.events:
        g = e.feeds_group
        if g is None:
            continue
        feed.setdefault(e.judge, [Duration.ZERO] * len(groups))[groups.index(g)] += e.duration

    def breeds_done(subset: int) -> TimeOfDay:
        """Earliest time every breed of the groups in ``subset`` can be judged."""
        return t0 + max(
            (Duration.total(w[i] for i in range(len(groups)) if subset >> i & 1) for w in feed.values()),
            default=Duration.ZERO,
        )

    def release(subset: int, k: int) -> TimeOfDay:
        """Earliest start of group k if the groups in ``subset`` (which includes k) go first."""
        r = breeds_done(subset)
        gj = show.group_judges.get(groups[k])
        if gj is not None and dur[groups[k]] > Duration.ZERO:
            r = max(r, free.get(gj, t0))
        return r

    # H13: the breeds of some `need` groups are all done before the first group starts.
    n = len(groups)
    entered = [i for i, g in enumerate(groups) if show.group_entrants(g) > 0]
    need = min(p.groups_complete_before_start, len(entered))
    first_start = min(
        (breeds_done(sum(1 << i for i in c)) for c in combinations(entered, need)), default=t0
    )

    # DP over subsets: best[S] = earliest time all groups in S can be finished, judged first.
    best: list[TimeOfDay | None] = [None] * (1 << n)
    prev: list[tuple[int, int] | None] = [None] * (1 << n)
    # H14 + H6: if any judge needs lunch, groups wait until it can have ended (window opens + lunch)
    lunches_over = t0
    if p.groups_after_all_lunches and any(judge_work(show, j) > p.lunch_required_after for j in show.judges):
        lunches_over = p.lunch_window[0] + p.lunch_min
    earliest = best[0] = max(t0, first_start, lunches_over)
    for s in range(1 << n):
        here = best[s]
        if here is None:
            continue
        for k in range(n):
            if s >> k & 1:
                continue
            s2 = s | 1 << k
            end = max(here, release(s2, k)) + dur[groups[k]]
            there = best[s2]
            if there is None or end < there:
                best[s2], prev[s2] = end, (s, k)
    full = (1 << n) - 1
    all_done = best[full]
    assert all_done is not None  # every subset is reachable from the empty one
    order: list[Group] = []
    step = prev[full]
    while step is not None:
        s, k = step
        order.append(groups[k])
        step = prev[s]
    order.reverse()

    starts: dict[Group, TimeOfDay] = {}
    t, s = earliest, 0
    for g in order:
        k = groups.index(g)
        s |= 1 << k
        starts[g] = max(t, release(s, k))
        t = starts[g] + dur[g]
    bis_ready = free.get(show.bis_judge, t0) if show.bis_judge else t0
    chain = max(all_done, bis_ready) + show.bis_duration()

    # ring capacity
    w_feed = Duration.total(x for w in feed.values() for x in w)
    r_all = len(show.rings)
    r_open = r_all - len(show.group_rings)
    total_groups = Duration.total(dur.values())
    before = max(Duration.ZERO, (w_feed - total_groups * r_open) / r_all) if r_all else Duration.ZERO
    capacity = t0 + before + total_groups + show.bis_duration()

    return FinishBound(chain, capacity, tuple(order), starts, free)
