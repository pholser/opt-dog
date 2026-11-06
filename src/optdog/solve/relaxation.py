"""A stronger lower bound on show finish: a CP-SAT relaxation of the scheduling problem.

It keeps every constraint that couples timing across judges, rings and groups, and drops
the ones about how judging is packaged:

kept
    - each judge judges one event at a time (H4), with their lunch in the window and
      judging before and after it (H6); pinned blocks are fixed commitments (H11)
    - rings as a pool: at most |R| events at once; from the first group start on, the
      group rings are taken (H9), so at most |R| − |RG| (pinned blocks included)
    - groups as in the CP-SAT model: after their breeds (H7) and their judge's last event
      (H8), one at a time (H10), the first at the published start F on the time grid; F
      after the breeds of at least two groups (H13) and every lunch (H14); BIS last

dropped
    - blocks (H1, H5): events need not be contiguous or about an hour
    - the time grid for blocks and lunches, travel margins (H4), ring identity
    - running order, equipment and every soft cost

Durations are rounded *down* to tenths of a minute, so the bound is never above the
CP-SAT model's or the validator's finish. Any schedule that satisfies the hard constraints
satisfies the kept ones, so CP-SAT's proven bound on this model is a lower bound on the
finish of every valid schedule. It is reported together with the combinatorial bound of
``bounds.finish_lower_bound``, and the larger of the two is used.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from optdog.domain import VARIETY_GROUPS, Show
from optdog.params import ShowParameters
from optdog.solve.bounds import finish_lower_bound

SCALE = 10


def _f(minutes: float) -> int:
    """Minutes → tenths, rounded down (never longer than planned)."""
    return int(math.floor(minutes * SCALE + 1e-6))


@dataclass(frozen=True)
class RelaxedBound:
    value: float  # the lower bound on BIS end: max(relaxation's proven bound, combinatorial bound)
    relaxation: float  # CP-SAT's proven bound on the relaxation (minutes after midnight)
    combinatorial: float  # bounds.finish_lower_bound(...).value
    relaxed_finish: float | None  # the relaxation's best schedule (≥ relaxation; equal if optimal)
    status: str
    seconds: float

    @property
    def optimal(self) -> bool:
        return self.status == "OPTIMAL"


def relaxed_lower_bound(show: Show, params: ShowParameters | None = None,
                        time_limit: float = 60.0, workers: int = 8) -> RelaxedBound:
    p = (params or ShowParameters()).for_show(show)
    md = cp_model.CpModel()
    t0 = show.start * SCALE
    H = t0 + 18 * 60 * SCALE
    slot = p.slot_minutes * SCALE
    rings = len(show.rings)
    group_rings = set(show.group_rings)
    pinned_ids = {id(e) for sb in show.pinned for e in sb.block.events}

    # events
    start, end = {}, {}
    judge_iv = {j: [] for j in show.judges}
    pool, demand = [], []
    for e in show.events:
        if id(e) in pinned_ids:
            continue
        u, d = id(e), _f(e.duration)
        s = start[u] = md.NewIntVar(t0, H - d, f"s_{u}")
        end[u] = s + d
        iv = md.NewFixedSizeIntervalVar(s, d, f"iv_{u}")
        judge_iv[e.judge].append(iv)
        pool.append(iv)
        demand.append(1)
    for sb in show.pinned:
        s0 = sb.start * SCALE
        for e, off in sb.block.offsets():
            start[id(e)] = _f(sb.start + off)
            end[id(e)] = _f(sb.start + off) + _f(e.duration)
        iv = md.NewFixedSizeIntervalVar(s0, _f(sb.block.duration), f"pin_{id(sb)}")
        judge_iv[sb.judge].append(iv)
        if sb.ring in show.rings:  # an obedience commitment's ring is not one of the pool's
            pool.append(iv)
            demand.append(1)

    # first group start F, on the grid
    fk = md.NewIntVar(t0 // slot, H // slot, "first_k")
    first = md.NewIntVar(t0, H, "first")
    md.Add(first == slot * fk)

    # H9: from F on, the group rings are not available for breed judging
    if group_rings:
        taken = md.NewIntervalVar(first, md.NewIntVar(0, H - t0, "taken_len"), H, "group_rings")
        pool.append(taken)
        demand.append(len(group_rings))
    md.AddCumulative(pool, demand, rings)
    for sb in show.pinned:
        if sb.ring in group_rings:
            md.Add(first >= _f(sb.end))

    # H6: lunch in the window, judging before and after it; H14: every lunch over by F
    l0, l1 = p.lunch_window[0] * SCALE, p.lunch_window[1] * SCALE
    lunch_len = p.lunch_min_minutes * SCALE
    for j in show.judges:
        events = show.events_for(j)
        if sum(e.duration for e in events) <= p.lunch_required_after_minutes:
            continue
        ls = md.NewIntVar(l0, l1, f"ls_{j.name}")
        judge_iv[j].append(md.NewFixedSizeIntervalVar(ls, lunch_len, f"lunch_{j.name}"))
        if p.groups_after_all_lunches:
            md.Add(first >= ls + lunch_len)
        free = [e for e in events if id(e) not in pinned_ids]
        pins = [sb for sb in show.pinned if sb.judge == j]
        bef = []
        for e in free:
            b = md.NewBoolVar(f"before_{id(e)}")
            md.Add(end[id(e)] <= ls).OnlyEnforceIf(b)
            md.Add(start[id(e)] >= ls + lunch_len).OnlyEnforceIf(b.Not())
            bef.append(b)
        if not any(sb.end * SCALE <= l0 for sb in pins):
            md.AddBoolOr(bef or [md.NewConstant(0)])
        if not any(sb.start * SCALE >= l1 + lunch_len for sb in pins):
            md.AddBoolOr([b.Not() for b in bef] or [md.NewConstant(0)])
    for ivs in judge_iv.values():
        md.AddNoOverlap(ivs)

    # groups: H7, H8, H10; F = earliest group start; BIS after every group
    def judge_done(j) -> list:
        return [end[id(e)] for e in show.events_for(j)]

    groups = [g for g in VARIETY_GROUPS if show.group_duration(g) > 0]
    tau, group_iv = {}, []
    for g in groups:
        d = _f(show.group_duration(g))
        t = tau[g] = md.NewIntVar(t0, H, f"tau_{g.name}")
        group_iv.append(md.NewFixedSizeIntervalVar(t, d, f"gv_{g.name}"))
        for e in show.feeding(g):
            md.Add(t >= end[id(e)])
        gj = show.group_judges.get(g)
        if gj is not None:
            for x in judge_done(gj):
                md.Add(t >= x)
    md.AddNoOverlap(group_iv)
    if groups:
        md.AddMinEquality(first, [tau[g] for g in groups])
    bis = md.NewIntVar(t0, H, "bis")
    md.Add(bis >= first)
    for g in groups:
        md.Add(bis >= tau[g] + _f(show.group_duration(g)))
    if show.bis_judge is not None:
        for x in judge_done(show.bis_judge):
            md.Add(bis >= x)

    # H13: the breeds of at least `need` groups are done by F
    entered = [g for g in VARIETY_GROUPS if show.group_entrants(g) > 0]
    need = min(p.groups_complete_before_start, len(entered))
    if need:
        done = []
        for g in entered:
            dg = md.NewBoolVar(f"done_{g.name}")
            for e in show.feeding(g):
                md.Add(end[id(e)] <= first).OnlyEnforceIf(dg)
            done.append(dg)
        md.Add(sum(done) >= need)

    finish = bis + _f(show.bis_duration())
    combinatorial = finish_lower_bound(show, p).value
    md.Add(finish >= math.floor(combinatorial * SCALE))  # a valid cut: helps the search prove
    md.Minimize(finish)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    t = time.perf_counter()
    status = solver.Solve(md)
    secs = time.perf_counter() - t
    good = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    relax = solver.BestObjectiveBound() / SCALE if good or status == cp_model.UNKNOWN else combinatorial
    return RelaxedBound(max(relax, combinatorial), relax, combinatorial,
                        solver.ObjectiveValue() / SCALE if good else None, solver.StatusName(status), secs)
