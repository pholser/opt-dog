"""CP-SAT placement model, step 1: blocks are given, the solver places them.

Given each judge's blocks in a fixed order (by default the greedy baseline's), decide:

- each block's **ring** and **start** (on the published time grid),
- each lunch's **start**, for judges who need one,
- the **published group start** and the **group order**,

subject to every hard constraint of SPEC §4, minimizing **show finish** (BIS end).

Model (time in tenths of a minute, so 2.4 min/dog is exact):

    start_b = slot · k_b                      block b on the grid (H5: "not before" times)
    Σ_r x_{b,r} = 1                           one ring per block
    ring r:  NoOverlap(optional intervals of blocks with x_{b,r}, pinned blocks in r)   (H3)
    judge j: NoOverlap(blocks, pinned ± travel, lunch)                                   (H4)
             start_{b+1} ≥ end_b + m_J·(1 − same_b)   consecutive blocks, travel if rings differ (H4)
    lunch:   L0 ≤ ls_j ≤ L1, length = lunch min; some judging before and after it        (H6)
    group g: τ_g ≥ end of every event feeding g                                          (H7)
             τ_g ≥ end of group judge's last block + m_J if it is outside the group rings (H8)
    first group start F (on the grid) ≤ τ_g for all g
             F ≥ end of every block placed in a group ring (+ set-up)                    (H9)
             F ≥ every event of ≥ 2 groups (chosen by the solver)                         (H13)
             F ≥ every judge's lunch end                                                 (H14)
    groups:  NoOverlap(group intervals); BIS starts after every group                    (H10)

H1 (block length) holds by construction of the blocks; H11 by the pinned blocks; H12 and
V1 are input checks. Phase 1 minimizes the BIS end. Phase 2 keeps that finish and only
tidies the schedule: fewest judge ring changes, then blocks as early as possible.
Both phases start from the greedy schedule (a complete, feasible solution hint).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from optdog.domain import (
    VARIETY_GROUPS,
    Block,
    Break,
    Group,
    GroupsPlan,
    JudgingProgram,
    Judge,
    Ring,
    ScheduledBlock,
    Show,
)
from optdog.params import ShowParameters
from optdog.solve.greedy import greedy_program

SCALE = 10  # model time unit = 1/10 minute


def _u(minutes: float) -> int:
    """Minutes → model units, rounding durations up (never shorter than planned)."""
    return int(math.ceil(minutes * SCALE - 1e-6))


@dataclass(frozen=True)
class PlacementOptions:
    time_limit: float = 60.0  # seconds per phase
    workers: int = 8
    hint: bool = True  # start from the greedy schedule
    tidy: bool = True  # phase 2: same finish, fewer ring changes, earlier blocks
    log: bool = False


@dataclass
class PhaseStats:
    name: str
    status: str
    objective: float | None
    bound: float | None
    seconds: float


@dataclass
class PlacementResult:
    program: JudgingProgram | None
    finish: float | None  # BIS end, minutes after midnight (model value)
    bound: float | None  # CP-SAT's proven lower bound on the finish for these blocks
    phases: list[PhaseStats] = field(default_factory=list)

    @property
    def optimal(self) -> bool:
        return bool(self.phases) and self.phases[0].status == "OPTIMAL"


def blocks_from_program(program: JudgingProgram) -> dict[Judge, list[Block]]:
    """Each judge's unpinned blocks, in the order they are judged in ``program``."""
    pinned = {id(sb) for sb in program.show.pinned}
    out: dict[Judge, list[Block]] = defaultdict(list)
    for sb in sorted(program.blocks, key=lambda b: b.start):
        if id(sb) not in pinned:
            out[sb.judge].append(sb.block)
    return dict(out)


def solve_placement(show: Show, params: ShowParameters | None = None,
                    blocks: dict[Judge, list[Block]] | None = None,
                    options: PlacementOptions | None = None) -> PlacementResult:
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    greedy = greedy_program(show, p)
    if blocks is None:
        blocks = blocks_from_program(greedy)
    hint_prog = greedy if blocks_from_program(greedy) == blocks else None

    m = _Model(show, p, blocks)
    if o.hint and hint_prog is not None:
        m.hint(hint_prog)

    res = PlacementResult(None, None, None)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = o.workers
    solver.parameters.max_time_in_seconds = o.time_limit
    solver.parameters.log_search_progress = o.log

    # phase 1: earliest finish
    m.model.Minimize(m.finish)
    t = time.perf_counter()
    status = solver.Solve(m.model)
    res.phases.append(PhaseStats("finish", solver.StatusName(status),
                                 solver.ObjectiveValue() / SCALE if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                                 solver.BestObjectiveBound() / SCALE, time.perf_counter() - t))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return res
    best_finish = solver.Value(m.finish)
    res.finish = best_finish / SCALE
    res.bound = solver.BestObjectiveBound() / SCALE
    m.keep_solution_as_hint(solver)

    # phase 2: same finish; fewest ring changes, then earliest blocks
    if o.tidy:
        m.model.Add(m.finish <= best_finish)
        horizon = m.horizon
        m.model.Minimize(sum(m.changes) * (len(m.starts) + 1) * horizon + sum(m.starts) + m.first)
        t = time.perf_counter()
        status2 = solver.Solve(m.model)
        res.phases.append(PhaseStats("tidy", solver.StatusName(status2),
                                     solver.ObjectiveValue() if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                                     solver.BestObjectiveBound(), time.perf_counter() - t))
        if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            status2 = solver.Solve(m.model)  # fall back to the phase-1 hint
    res.program = m.extract(solver)
    return res


class _Model:
    def __init__(self, show: Show, p: ShowParameters, blocks: dict[Judge, list[Block]]):
        self.show, self.p, self.blocks = show, p, blocks
        md = self.model = cp_model.CpModel()
        slot = p.slot_minutes * SCALE
        t0 = show.start * SCALE
        self.horizon = H = t0 + 18 * 60 * SCALE
        travel = _u(p.judge_travel_minutes)
        rings = list(show.rings)
        group_rings = set(show.group_rings)
        self.rings = rings

        # ------------------------------------------------------------------ blocks
        self.k: dict[int, cp_model.IntVar] = {}
        self.start: dict[int, cp_model.IntVar] = {}
        self.end: dict[int, cp_model.LinearExpr] = {}
        self.dur: dict[int, int] = {}
        self.x: dict[tuple[int, Ring], cp_model.IntVar] = {}
        self.block_of: dict[int, Block] = {}
        ring_iv: dict[Ring, list] = defaultdict(list)
        judge_iv: dict[Judge, list] = defaultdict(list)
        self.starts: list = []
        for j, bl in blocks.items():
            for b in bl:
                i = id(b)
                self.block_of[i] = b
                d = self.dur[i] = _u(b.duration)
                k = self.k[i] = md.NewIntVar(t0 // slot, (H - d) // slot, f"k_{i}")
                s = self.start[i] = md.NewIntVar(t0, H, f"s_{i}")
                md.Add(s == slot * k)
                self.starts.append(s)
                self.end[i] = s + d
                judge_iv[j].append(md.NewFixedSizeIntervalVar(s, d, f"jv_{i}"))
                xs = []
                for r in rings:
                    x = self.x[i, r] = md.NewBoolVar(f"x_{i}_{r.number}")
                    ring_iv[r].append(md.NewOptionalFixedSizeIntervalVar(s, d, x, f"rv_{i}_{r.number}"))
                    xs.append(x)
                md.AddExactlyOne(xs)

        pinned_spans: dict[Judge, list[list[int]]] = defaultdict(list)
        for sb in sorted(show.pinned, key=lambda b: b.start):
            s, d = sb.start * SCALE, _u(sb.block.duration)
            ring_iv[sb.ring].append(md.NewFixedSizeIntervalVar(s, d, f"pin_r_{id(sb)}"))
            # the judge is busy from `travel` before to `travel` after; merge back-to-back
            # commitments so that their padded spans do not overlap each other
            spans = pinned_spans[sb.judge]
            lo, hi = s - travel, s + d + travel
            if spans and lo <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], hi)
            else:
                spans.append([lo, hi])
        for j, spans in pinned_spans.items():
            for n, (lo, hi) in enumerate(spans):
                judge_iv[j].append(md.NewFixedSizeIntervalVar(lo, hi - lo, f"pin_j_{j.name}_{n}"))

        # judge chains: order, travel margin when the ring changes
        self.changes: list = []
        self.same: dict[tuple[int, int], cp_model.IntVar] = {}
        for j, bl in blocks.items():
            for a, b in zip(bl, bl[1:]):
                ia, ib = id(a), id(b)
                same = self.same[ia, ib] = md.NewBoolVar(f"same_{ia}_{ib}")
                for r in rings:
                    md.Add(self.x[ia, r] == self.x[ib, r]).OnlyEnforceIf(same)
                md.Add(self.start[ib] >= self.end[ia] + travel).OnlyEnforceIf(same.Not())
                md.Add(self.start[ib] >= self.end[ia])
                self.changes.append(same.Not())

        # ------------------------------------------------------------------ lunch (H6)
        l0, l1 = p.lunch_window[0] * SCALE, p.lunch_window[1] * SCALE
        lunch_len = p.lunch_min_minutes * SCALE
        self.lunch: dict[Judge, cp_model.IntVar] = {}
        for j in show.judges:
            work = sum(e.duration for e in show.events_for(j))
            if work <= p.lunch_required_after_minutes:
                continue
            bl = blocks.get(j, [])
            pins = [sb for sb in show.pinned if sb.judge == j]
            ls = md.NewIntVar(-(-l0 // slot), l1 // slot, f"lunch_k_{j.name}")
            lstart = md.NewIntVar(l0, l1, f"ls_{j.name}")
            md.Add(lstart == slot * ls)
            judge_iv[j].append(md.NewFixedSizeIntervalVar(lstart, lunch_len, f"lv_{j.name}"))
            self.lunch[j] = lstart
            # judging both before and after the lunch (a break *in* the assignment)
            if not any(sb.end * SCALE <= l0 for sb in pins):
                if not bl:
                    raise ValueError(f"{j.name} needs lunch but has no movable blocks before it")
                md.Add(self.end[id(bl[0])] <= lstart)
            if not any(sb.start * SCALE >= l1 + lunch_len for sb in pins):
                if not bl:
                    raise ValueError(f"{j.name} needs lunch but has no movable blocks after it")
                md.Add(self.start[id(bl[-1])] >= lstart + lunch_len)

        for r, ivs in ring_iv.items():
            md.AddNoOverlap(ivs)
        for j, ivs in judge_iv.items():
            md.AddNoOverlap(ivs)

        # ------------------------------------------------------------------ groups
        offset_end: dict[int, tuple[int, int]] = {}  # event id → (block id, end offset in units)
        for i, b in self.block_of.items():
            for e, off in b.offsets():
                offset_end[id(e)] = (i, _u(off + e.duration))
        pinned_event_end = {id(e): _u(sb.start + off + e.duration)
                            for sb in show.pinned for e, off in sb.block.offsets()}

        def event_end(e):
            if id(e) in offset_end:
                i, oe = offset_end[id(e)]
                return self.start[i] + oe
            return pinned_event_end[id(e)]

        groups = [g for g in VARIETY_GROUPS if show.group_duration(g) > 0]
        self.groups = groups
        self.tau: dict[Group, cp_model.IntVar] = {}
        group_iv = []
        first_k = md.NewIntVar(t0 // slot, H // slot, "first_k")
        self.first = md.NewIntVar(t0, H, "first")
        md.Add(self.first == slot * first_k)
        setup = _u(p.group_ring_setup_minutes)

        def judge_free_terms(j: Judge):
            """Lower bounds on when ``j`` can be in the group ring (H8)."""
            terms = []
            bl = blocks.get(j, [])
            if bl:
                last = id(bl[-1])
                in_gr = sum(self.x[last, r] for r in group_rings) if group_rings else 0
                terms.append(self.end[last] + travel - travel * in_gr)
            for sb in show.pinned:
                if sb.judge == j:
                    terms.append(_u(sb.end) + (0 if sb.ring in group_rings else travel))
            return terms

        for g in groups:
            d = _u(show.group_duration(g))
            tau = self.tau[g] = md.NewIntVar(t0, H, f"tau_{g.name}")
            group_iv.append(md.NewFixedSizeIntervalVar(tau, d, f"gv_{g.name}"))
            md.Add(tau >= self.first)
            for e in show.feeding(g):  # H7
                md.Add(tau >= event_end(e))
            gj = show.group_judges.get(g)
            if gj is not None:  # H8
                for term in judge_free_terms(gj):
                    md.Add(tau >= term)
        md.AddNoOverlap(group_iv)  # H10
        bis_d = _u(show.bis_duration())
        self.bis = md.NewIntVar(t0, H, "bis")
        for g in groups:
            md.Add(self.bis >= self.tau[g] + _u(show.group_duration(g)))
        md.Add(self.bis >= self.first)
        if show.bis_judge is not None:
            for term in judge_free_terms(show.bis_judge):
                md.Add(self.bis >= term)
        self.finish = self.bis + bis_d

        # H9: every group ring clear before the first group
        for i in self.block_of:
            for r in group_rings:
                md.Add(self.end[i] + setup <= self.first).OnlyEnforceIf(self.x[i, r])
        for sb in show.pinned:
            if sb.ring in group_rings:
                md.Add(self.first >= _u(sb.end) + setup)

        # H13: the breeds of at least `need` groups are done by the first group
        entered = [g for g in VARIETY_GROUPS if show.group_entrants(g) > 0]
        need = min(p.groups_complete_before_start, len(entered))
        if need:
            done = []
            for g in entered:
                dg = md.NewBoolVar(f"done_{g.name}")
                for e in show.feeding(g):
                    md.Add(event_end(e) <= self.first).OnlyEnforceIf(dg)
                done.append(dg)
            md.Add(sum(done) >= need)

        # H14: every lunch over by the first group
        if p.groups_after_all_lunches:
            for ls in self.lunch.values():
                md.Add(self.first >= ls + lunch_len)

    # ---------------------------------------------------------------------- hints
    def hint(self, prog: JudgingProgram) -> None:
        md, slot = self.model, self.p.slot_minutes * SCALE
        by_block = {id(sb.block): sb for sb in prog.blocks}
        for i in self.block_of:
            sb = by_block[i]
            md.AddHint(self.k[i], sb.start * SCALE // slot)
            md.AddHint(self.start[i], sb.start * SCALE)
            for r in self.rings:
                md.AddHint(self.x[i, r], int(sb.ring == r))
        for (ia, ib), v in self.same.items():
            md.AddHint(v, int(by_block[ia].ring == by_block[ib].ring))
        for br in prog.breaks:
            if br.judge in self.lunch:
                md.AddHint(self.lunch[br.judge], br.start * SCALE)

    def keep_solution_as_hint(self, solver: cp_model.CpSolver) -> None:
        md = self.model
        md.clear_hints()
        for idx in range(len(md.proto.variables)):
            var = md.get_int_var_from_proto_index(idx)
            md.add_hint(var, solver.value(var))

    # ---------------------------------------------------------------------- solution
    def extract(self, solver: cp_model.CpSolver) -> JudgingProgram:
        show = self.show
        out: list[ScheduledBlock] = list(show.pinned)
        placed: dict[int, ScheduledBlock] = {}
        for i, b in self.block_of.items():
            ring = next(r for r in self.rings if solver.Value(self.x[i, r]))
            sb = ScheduledBlock(b, ring, solver.Value(self.start[i]) // SCALE)
            placed[i] = sb
            out.append(sb)
        breaks = []
        for j, ls in self.lunch.items():
            start = solver.Value(ls) // SCALE
            before = [sb for sb in out if sb.judge == j and sb.start < start]
            ring = max(before, key=lambda sb: sb.start).ring if before else None
            breaks.append(Break(j, ring, start, self.p.lunch_min_minutes))
        order = tuple(sorted(self.groups, key=lambda g: solver.Value(self.tau[g])))
        order += tuple(g for g in VARIETY_GROUPS if g not in order)  # groups with no entries
        plan = GroupsPlan(show.group_rings, solver.Value(self.first) // SCALE, order,
                          dict(show.group_judges), show.bis_judge)
        return JudgingProgram(show, out, breaks, plan)
