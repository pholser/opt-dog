"""CP-SAT scheduling model: placement of given blocks (step 1), block formation (step 3a) and
running order (step 3b).

Each judge's events are split into **segments** with a fixed internal running order. Within
a segment, between two consecutive events a **cut** either starts a new block or continues
the current one. Segments are linked into one chain per judge by **arcs** (a circuit).

- Step 1 (``solve_placement``): one segment per judge, every cut fixed by the given blocks.
- Step 3a (``solve_formation``): one segment per judge (the greedy's running order); the
  solver chooses the cuts.
- Step 3b (``solve_ordering``): one segment per (group, equipment) of a judge, in the greedy's
  order inside each; the solver chooses the segments' order, so it orders the judge's groups
  and, within each group, whether e.g. table breeds come before or after ground breeds. A
  judge's group stays contiguous (S4, Show Manual §6 "organize their breed-judging schedule
  by group"); table breeds and ramp breeds stay consecutive within a group (S3).

It decides each block's **ring** and **start**, each lunch's **start**, the **published group
start** and the **group order**, subject to every hard constraint of SPEC §4, minimizing
**show finish** (BIS end).

Model (time in tenths of a minute, so 2.4 min/dog is exact; u⁻ is the event before u, fixed
inside a segment, chosen by an arc a_{PQ} at a segment's first event):

    circuit over each judge's segments; each group entered once                  (3b)
    c_u ∈ {0,1}: u starts a block (1 for a judge's first event)
    c_u = 0 ⇒ s_u = s_u⁻ + d_u⁻, ring(u) = ring(u⁻)           blocks are contiguous (H5)
    c_u = 1 ⇒ s_u = Δ·k_u                                      published "not before" grid
    s_u ≥ e_u⁻ + m_J·[ring(u) ≠ ring(u⁻)]                      travel margin (H4)
    b_u = s_u if c_u else b_u⁻;  e_u − b_u ≤ cap               about an hour (H1; 3a, 3b)
    ring r:  NoOverlap(optional intervals of events in r, pinned blocks in r)            (H3)
             an event is never in a ring smaller than its breed needs                    (H16)
    judge j: NoOverlap(events, pinned ± travel, lunch)                                   (H4)
    lunch:   L0 ≤ ℓ_j ≤ L1, length = lunch min; judging before and after it              (H6)
    group g: τ_g ≥ end of every event feeding g                                          (H7)
             τ_g ≥ end of group judge's last event + m_J if it is outside the group rings (H8)
    first group start F (on the grid) = min_g τ_g
             F ≥ end of every event placed in a group ring (+ set-up)                    (H9)
             F ≥ every event of ≥ 2 groups (chosen by the solver)                         (H13)
             F ≥ every judge's lunch end                                                 (H14)
    groups:  NoOverlap(group intervals); BIS starts after every group                    (H10)

H11 holds by the pinned blocks; H12 and V1 are input checks. Six lexicographic phases:
(1) minimize the BIS end; (2) minimize BIS end + `short_block_finish_minutes` × short blocks,
giving up finish for fewer short blocks (skipped if nothing can change the count, or the
parameter is 0); (3) at that finish and count, minimize the soft costs the structure controls
(S2 ring changes, S3 equipment switches, S9 short blocks, S10 lunch splitting a group, block
count); (4) at those, minimize judge idle (S5), then cap idle at `conflict_idle_budget` × that
minimum; (5) within the cap, minimize conflicts of exhibitors signed up for the accommodation
(S1); (6) at those, minimize judge idle again, then start blocks as early as possible. Phases
4 and 5 run only when some registered exhibitor has a pair that can clash. Each
phase starts from the previous one's solution; phase 1 starts from the greedy schedule
(step 1) or the step-1 schedule. Step 3c runs the same model with the breeds inside each run
packed into full blocks first (packing.py).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from optdog.conflicts import registered_window_pairs
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
from optdog.solve.greedy import greedy_program
from optdog.solve.packing import pack_run

SCALE = 10  # model time unit = 1/10 minute
WSCALE = 10  # soft-cost weights → integer coefficients
IDLE_OVER_STARTS = 10  # phase 5: one unit of judge idle outweighs ten units of later starts

Chain = list[JudgingEvent]
Cut = bool | None  # event starts a block (True), continues the block (False), or the solver chooses (None)


@dataclass(frozen=True)
class Segment:
    """Events judged consecutively in this order; ``cls`` names the group they belong to
    (3b: a judge's segments of one class are contiguous)."""

    events: tuple[JudgingEvent, ...]
    cuts: tuple[Cut, ...]
    cls: object = None


def _u(minutes: float) -> int:
    """Minutes → model units, rounding durations up (never shorter than planned)."""
    return int(math.ceil(minutes * SCALE - 1e-6))


@dataclass(frozen=True)
class PlacementOptions:
    time_limit: float = 60.0  # seconds per phase
    # phase 4 sets the idle budget, so a weak minimum widens it (Palm Springs 1/5 at 60 s: cap 511 min).
    # None: 3 × time_limit.
    idle_time_limit: float | None = None
    # phase 5 never proves a minimum (bound 0); at 60 s Greater Sierra Vista 11/17 left 245 min, at 180 s 81-102.
    # None: 3 × time_limit.
    conflict_time_limit: float | None = None
    workers: int = 8
    hint: bool = True  # start from a known schedule (greedy, or step 1 for formation)
    tidy: bool = True  # phases 3-6: at that finish, lower soft costs, then earlier blocks
    log: bool = False
    max_block_minutes: float = 65.0  # 3a-3c: H1 cap on a formed block; same as the greedy (E: human p90 27 dogs)


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
    finish: float | None  # BIS end of the returned schedule, minutes after midnight, as the validator times it
    bound: float | None  # CP-SAT's proven lower bound on the finish for this model (from phase 1)
    phases: list[PhaseStats] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)  # structure costs of the returned schedule
    idle: float | None = None  # judge idle of the returned schedule, minutes (model value, S5)
    idle_min: float | None = None  # least idle at that structure (phase 4), before the conflict budget

    @property
    def status(self) -> str:
        """How far the main objective was proven: the weakest of the finish and trade phases."""
        statuses = [ph.status for ph in self.phases if ph.name in ("finish", "trade")]
        return next((s for s in statuses if s != "OPTIMAL"), "OPTIMAL") if statuses else "UNKNOWN"

    @property
    def optimal(self) -> bool:
        return self.status == "OPTIMAL"


# --------------------------------------------------------------------------- chains


def blocks_from_program(program: JudgingProgram) -> dict[Judge, list[Block]]:
    """Each judge's unpinned blocks, in the order they are judged in ``program``."""
    pinned = {id(sb) for sb in program.show.pinned}
    out: dict[Judge, list[Block]] = defaultdict(list)
    for sb in sorted(program.blocks, key=lambda b: b.start):
        if id(sb) not in pinned:
            out[sb.judge].append(sb.block)
    return dict(out)


def chains_from_blocks(blocks: dict[Judge, list[Block]]) -> dict[Judge, list[Segment]]:
    """One segment per judge with every cut fixed by ``blocks`` (step 1)."""
    out = {}
    for j, bl in blocks.items():
        chain = tuple(e for b in bl for e in b.events)
        cuts = tuple(n == 0 for b in bl for n in range(len(b.events)))
        out[j] = [Segment(chain, cuts)]
    return out


def _free_cuts_of(chain: Chain, cap_minutes: float) -> list[Cut]:
    cuts: list[Cut] = [None] * len(chain)
    alone = [e.duration > cap_minutes for e in chain]
    for i in range(1, len(chain)):
        a, b = chain[i - 1], chain[i]
        if alone[i - 1] or alone[i]:
            cuts[i] = True
        elif a.kind is EventKind.SWEEPSTAKES and b.kind is EventKind.BREED and a.breed == b.breed:
            cuts[i] = False
    if chain and alone[0]:
        cuts[0] = True
    return cuts


def free_cuts(chains: dict[Judge, Chain], cap_minutes: float) -> dict[Judge, list[Segment]]:
    """One segment per judge with cuts for the solver to choose (step 3a), except that

    - an event longer than the block cap is a block of its own (H1 exception), and
    - a sweepstakes stays in the same block as the breed it precedes (H15), unless
      that breed must be alone.
    """
    out = {}
    for j, chain in chains.items():
        cuts = _free_cuts_of(chain, cap_minutes)
        cuts[0] = True
        out[j] = [Segment(tuple(chain), tuple(cuts))]
    return out


def _exam(e: JudgingEvent) -> str:
    if e.breed is None:
        return "ground"
    ex = e.breed.examination
    return "table" if ex.needs_table else "ramp" if ex.needs_ramp else "ground"


def segment_class(e: JudgingEvent) -> object:
    """The group an event's segment belongs to: its variety group (sweepstakes by their
    breed's), "junior" for Junior Showmanship, "other" for the rest (Miscellaneous, FSS,
    obedience...) — the same classes as the greedy's running order."""
    if e.kind is EventKind.JUNIOR_SHOWMANSHIP:
        return "junior"
    g = e.breed.group if e.breed else None
    return g if g is not None and g.is_variety_group else "other"


def ordering_segments(chains: dict[Judge, Chain], cap_minutes: float) -> dict[Judge, list[Segment]]:
    """Step 3b: each judge's events split by (group class, equipment), each keeping its order
    in ``chains``; cuts free as in step 3a."""
    out = {}
    for j, chain in chains.items():
        parts: dict[tuple, list[JudgingEvent]] = {}
        for e in chain:
            parts.setdefault((segment_class(e), _exam(e)), []).append(e)
        out[j] = [Segment(tuple(evs), tuple(_free_cuts_of(evs, cap_minutes)), key[0]) for key, evs in parts.items()]
        if len(out[j]) == 1:
            only = out[j][0]
            out[j] = [replace(only, cuts=(True,) + only.cuts[1:])]
    return out


def packed_blocks(chains: dict[Judge, Chain], cap_minutes: float, short_dogs: int) -> dict[Judge, list[Block]]:
    """Step 3c: each judge's runs, as in 3b, with their breeds packed into full blocks."""
    out = {}
    for j, chain in chains.items():
        parts: dict[tuple, list[JudgingEvent]] = {}
        for e in chain:
            parts.setdefault((segment_class(e), _exam(e)), []).append(e)
        out[j] = [Block(j, tuple(b)) for run in parts.values() for b in pack_run(run, cap_minutes, short_dogs)]
    return out


# --------------------------------------------------------------------------- solving


def solve_placement(show: Show, params: ShowParameters | None = None,
                    blocks: dict[Judge, list[Block]] | None = None,
                    options: PlacementOptions | None = None) -> PlacementResult:
    """Step 1: place given blocks (default: the greedy's) in rings and times."""
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    greedy = greedy_program(show, p)
    if blocks is None:
        blocks = blocks_from_program(greedy)
    hint_prog = greedy if blocks_from_program(greedy) == blocks else None
    m = _Model(show, p, chains_from_blocks(blocks), cap=None)
    return _solve(m, o, hint_prog)


def _greedy_start(show: Show, p: ShowParameters, o: PlacementOptions):
    greedy = greedy_program(show, p)
    blocks = blocks_from_program(greedy)
    start = solve_placement(show, p, blocks, replace(o, tidy=False))
    chains = {j: [e for b in bl for e in b.events] for j, bl in blocks.items()}
    return chains, start.program or greedy


def solve_formation(show: Show, params: ShowParameters | None = None,
                    options: PlacementOptions | None = None) -> PlacementResult:
    """Step 3a: keep each judge's running order (the greedy's), let the solver choose where
    blocks break, where lunch falls, and each block's ring and start."""
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    chains, hint = _greedy_start(show, p, o)
    m = _Model(show, p, free_cuts(chains, o.max_block_minutes), cap=o.max_block_minutes)
    return _solve(m, o, hint)


def solve_ordering(show: Show, params: ShowParameters | None = None,
                   options: PlacementOptions | None = None) -> PlacementResult:
    """Step 3b: as 3a, and the solver also orders each judge's groups and, within a group,
    its ground / table / ramp runs."""
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    chains, hint = _greedy_start(show, p, o)
    m = _Model(show, p, ordering_segments(chains, o.max_block_minutes), cap=o.max_block_minutes)
    return _solve(m, o, hint)


def _solve(m: "_Model", o: PlacementOptions, hint_prog: JudgingProgram | None) -> PlacementResult:
    if o.hint and hint_prog is not None:
        m.hint(hint_prog)
    res = PlacementResult(None, None, None)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = o.workers
    solver.parameters.max_time_in_seconds = o.time_limit
    solver.parameters.log_search_progress = o.log
    ok = (cp_model.OPTIMAL, cp_model.FEASIBLE)

    if o.hint and hint_prog is not None:
        # The hint leaves auxiliary variables open, and on a large model CP-SAT can lose it
        # within a short limit. Completing it first makes every phase start from a full solution.
        m.model.Minimize(m.finish)
        solver.parameters.fix_variables_to_their_hinted_value = True
        if solver.Solve(m.model) in ok:
            m.keep_solution_as_hint(solver)
        solver.parameters.fix_variables_to_their_hinted_value = False

    def run(name: str, objective, scale: float, limit: float | None = None) -> bool:
        m.model.Minimize(objective)
        solver.parameters.max_time_in_seconds = o.time_limit if limit is None else limit
        t = time.perf_counter()
        status = solver.Solve(m.model)
        good = status in ok
        res.phases.append(PhaseStats(name, solver.StatusName(status),
                                     solver.ObjectiveValue() / scale if good else None,
                                     solver.BestObjectiveBound() / scale if good else None,
                                     time.perf_counter() - t))
        if good:
            m.keep_solution_as_hint(solver)
        return good

    # phase 1: earliest finish
    if not run("finish", m.finish, SCALE):
        return res
    best_finish = solver.Value(m.finish)
    res.bound = solver.BestObjectiveBound() / SCALE
    floor = math.ceil(solver.BestObjectiveBound() - 1e-6)

    # phase 2: give up finish for fewer short blocks
    trade = _u(m.p.short_block_finish_minutes)
    if trade > 0 and m.short_flags:
        short = solver.Value(m.short_blocks)
        m.model.Add(m.finish >= floor)  # valid cuts: no better trade lies outside these
        m.model.Add(m.finish <= best_finish + trade * short)
        if run("trade", m.finish + trade * m.short_blocks, SCALE):
            best_finish = solver.Value(m.finish)
            m.model.Add(m.short_blocks <= solver.Value(m.short_blocks))
    res.finish = best_finish / SCALE
    m.model.Add(m.finish <= best_finish)

    if o.tidy:
        # phase 3: soft costs of the structure, at that finish
        if run("structure", m.structure, WSCALE):
            m.model.Add(m.structure <= solver.Value(m.structure))
            times = IDLE_OVER_STARTS * m.idle + sum(m.run_starts) + m.first
            conflicts = m.conflict_cost()
            if conflicts is not None:
                # phase 4: least judge idle, which then may grow by the budget
                if run("idle", times, SCALE, o.idle_time_limit or 3 * o.time_limit):
                    res.idle_min = solver.Value(m.idle) / SCALE
                    budget = max(1.0, m.p.conflict_idle_budget)
                    m.model.Add(m.idle <= math.floor(solver.Value(m.idle) * budget + 1e-6))
                # phase 5: exhibitor conflicts, for those signed up for the accommodation
                if run("conflicts", conflicts, SCALE, o.conflict_time_limit or 3 * o.time_limit):
                    m.model.Add(conflicts <= solver.Value(conflicts))
            # phase 6: least judge idle, then earliest blocks
            run("times", times, SCALE)
    if res.phases[-1].status not in ("OPTIMAL", "FEASIBLE"):
        m.model.Minimize(m.finish)  # fall back to the last good solution (kept as the hint)
        solver.Solve(m.model)
    res.program = m.extract(solver)
    # Group starts are only bounded below, so a phase that stops at its limit can leave them
    # later than needed. Report the finish the program itself gives, as the validator times it.
    timeline = res.program.group_timeline(judge_travel_minutes=m.p.judge_travel_minutes)
    if timeline is not None:
        res.finish = round(timeline.finish, 1)  # the model works in tenths of a minute
    res.costs = {k: float(solver.Value(v)) for k, v in m.cost_terms.items()}
    res.idle = solver.Value(m.idle) / SCALE
    return res


def solve_packing(show: Show, params: ShowParameters | None = None,
                  options: PlacementOptions | None = None) -> PlacementResult:
    """Step 3c: as 3b, with the breeds inside each run ordered to pack into full blocks."""
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    chains, _ = _greedy_start(show, p, o)
    blocks = packed_blocks(chains, o.max_block_minutes, p.short_block_dogs)
    hint = solve_placement(show, p, blocks, replace(o, tidy=False)).program
    packed = {j: [e for b in bl for e in b.events] for j, bl in blocks.items()}
    m = _Model(show, p, ordering_segments(packed, o.max_block_minutes), cap=o.max_block_minutes)
    return _solve(m, o, hint)


# --------------------------------------------------------------------------- model


class _Model:
    def __init__(self, show: Show, p: ShowParameters, chains: dict[Judge, list[Segment]], cap: float | None):
        self.show, self.p, self.chains = show, p, chains
        md = self.model = cp_model.CpModel()
        slot = self.slot = p.slot_minutes * SCALE
        t0 = self.t0 = show.start * SCALE
        H = self.H = t0 + 18 * 60 * SCALE
        travel = _u(p.judge_travel_minutes)
        rings = self.rings = list(show.rings)
        group_rings = set(show.group_rings)
        w = p.weights

        # ------------------------------------------------------------------ order of segments (3b)
        # arcs[j][(p, q)]: segment q follows segment p (1-based; 0 is the start/end of the day)
        self.arcs: dict[Judge, dict[tuple[int, int], object]] = {}
        self.preds: dict[int, list[tuple[int | None, object]]] = {}  # segment-first event id → (pred event id, arc)
        for j, segs in chains.items():
            n = len(segs)
            if n == 1:
                arcs = {(0, 1): True, (1, 0): True}
            else:
                arcs = {(a, b): md.NewBoolVar(f"arc_{j.name}_{a}_{b}")
                        for a in range(n + 1) for b in range(n + 1) if a != b}
                md.AddCircuit([(a, b, lit) for (a, b), lit in arcs.items()])
                # each group class is entered once: its segments are contiguous
                for cls in {s.cls for s in segs}:
                    inside = {q for q in range(1, n + 1) if segs[q - 1].cls == cls}
                    md.Add(sum(lit for (a, b), lit in arcs.items() if b in inside and a not in inside) == 1)
            self.arcs[j] = arcs
            for q, seg in enumerate(segs, 1):
                self.preds[id(seg.events[0])] = [(None if a == 0 else id(segs[a - 1].events[-1]), lit)
                                                 for (a, b), lit in arcs.items() if b == q]

        # ------------------------------------------------------------------ events
        # per event id: start s (var or var + const), cut c (bool or literal), ring literals x
        self.s: dict[int, cp_model.LinearExprT] = {}
        self.d: dict[int, int] = {}
        self.c: dict[int, object] = {}
        self.k: dict[int, cp_model.IntVar] = {}
        self.x: dict[int, dict[Ring, cp_model.IntVar]] = {}
        self.same: dict[int, cp_model.IntVar] = {}  # event id → literal "same ring as the event before"
        self.bs: dict[int, cp_model.IntVar] = {}  # block start (3a, 3b)
        self.acc: dict[int, cp_model.IntVar] = {}  # dogs so far in the block (3a, 3b)
        self.prev: dict[int, int] = {}  # event id → fixed predecessor's id (inside a segment)
        self.run_starts: list = []
        ring_iv: dict[Ring, list] = defaultdict(list)
        judge_iv: dict[Judge, list] = defaultdict(list)
        self.runs: list[tuple[list[int], object]] = []  # (event ids, judge) of each fixed-contiguous run
        changes: list[tuple[Judge, int, object]] = []  # (judge, event id, "ring changed before it")
        short_terms = []
        self.short_flags: list = []  # one per block that closes short, for the phase 2 trade
        n_blocks = []
        free_of: dict[Judge, bool] = {}

        for j, segs in chains.items():
            # blocks formed by the model (3a-3c), even if every cut of this judge is forced
            free = free_of[j] = cap is not None or any(c is None for s in segs for c in s.cuts)
            total_dogs = sum(e.entries for s in segs for e in s.events)
            for seg in segs:
                run: list[int] = []
                for i, (e, cut) in enumerate(zip(seg.events, seg.cuts)):
                    u = id(e)
                    d = self.d[u] = _u(e.duration)
                    prev = id(seg.events[i - 1]) if i else None
                    if prev is not None:
                        self.prev[u] = prev
                    if cut is False:
                        c = self.c[u] = False
                        self.s[u] = self.s[prev] + self.d[prev]
                        self.x[u] = self.x[prev]
                    else:
                        c = self.c[u] = True if cut else md.NewBoolVar(f"c_{u}")
                        s = self.s[u] = md.NewIntVar(t0, H - d, f"s_{u}")
                        k = self.k[u] = md.NewIntVar(t0 // slot, (H - d) // slot, f"k_{u}")
                        if c is True:
                            md.Add(s == slot * k)
                        else:
                            md.Add(s == slot * k).OnlyEnforceIf(c)
                            if prev is not None:
                                md.Add(s == self.s[prev] + self.d[prev]).OnlyEnforceIf(c.Not())
                        xs = self.x[u] = {r: md.NewBoolVar(f"x_{u}_{r.number}") for r in rings}
                        md.AddExactlyOne(xs.values())
                        if prev is not None:
                            same = self.same[u] = md.NewBoolVar(f"same_{u}")
                            for r in rings:
                                md.Add(xs[r] == self.x[prev][r]).OnlyEnforceIf(same)
                            md.Add(s >= self.s[prev] + self.d[prev] + travel).OnlyEnforceIf(same.Not())
                            md.Add(s >= self.s[prev] + self.d[prev])
                            if c is not True:
                                md.AddImplication(c.Not(), same)
                            changes.append((j, u, same.Not()))
                        if run:
                            self.runs.append((run, j))
                        run = []
                        self.run_starts.append(s)
                    run.append(u)
                    n_blocks.append(c)
                    judge_iv[j].append(md.NewFixedSizeIntervalVar(self.s[u], d, f"jv_{u}"))

                    if free:  # block start and dogs so far, for H1 and S9
                        bs = self.bs[u] = md.NewIntVar(t0, H, f"bs_{u}")
                        acc = self.acc[u] = md.NewIntVar(0, total_dogs, f"acc_{u}")
                        if c is not False:
                            starts_here = [] if c is True else [c]
                            md.Add(bs == self.s[u]).OnlyEnforceIf(starts_here)
                            md.Add(acc == e.entries).OnlyEnforceIf(starts_here)
                        if c is not True and prev is not None:
                            cont = [] if c is False else [c.Not()]
                            md.Add(bs == self.bs[prev]).OnlyEnforceIf(cont)
                            md.Add(acc == self.acc[prev] + e.entries).OnlyEnforceIf(cont)
                        if cap is not None and e.duration <= cap:
                            md.Add(self.s[u] + d - bs <= _u(cap))
                if run:
                    self.runs.append((run, j))

        # H16: no event in a ring smaller than its breed needs
        need = {id(e): e.min_ring for segs in chains.values() for seg in segs for e in seg.events}
        for u, xs in self.x.items():
            for r, lit in xs.items():
                if r.size < need[u]:
                    md.Add(lit == 0)

        # links between segments: a segment's first event follows the segment its arc names
        for j, segs in chains.items():
            for seg in segs:
                u = id(seg.events[0])
                real = [(pu, a) for pu, a in self.preds[u] if pu is not None]
                starts = [a for pu, a in self.preds[u] if pu is None]
                c, s, xs = self.c[u], self.s[u], self.x[u]
                for a in starts:  # the judge's first event starts a block
                    if c is not True:
                        md.AddBoolOr([c]) if a is True else md.AddImplication(a, c)
                if not real:
                    continue
                same = self.same[u] = md.NewBoolVar(f"same_{u}")
                if c is not True:
                    md.AddImplication(c.Not(), same)
                for a in starts:
                    md.AddImplication(a, same)  # no ring change before a judge's first event
                for pu, a in real:
                    end = self.s[pu] + self.d[pu]
                    md.Add(s >= end).OnlyEnforceIf(a)
                    md.Add(s >= end + travel).OnlyEnforceIf([a, same.Not()])
                    for r in rings:
                        md.Add(xs[r] == self.x[pu][r]).OnlyEnforceIf([a, same])
                    if cap is not None and self.d[pu] > _u(cap) and c is not True:
                        md.AddImplication(a, c)  # an event too long for a block is alone
                    if c is not True:
                        md.Add(s == end).OnlyEnforceIf([a, c.Not()])
                        if free_of[j]:
                            md.Add(self.bs[u] == self.bs[pu]).OnlyEnforceIf([a, c.Not()])
                            md.Add(self.acc[u] == self.acc[pu] + seg.events[0].entries).OnlyEnforceIf([a, c.Not()])
                changes.append((j, u, same.Not()))

        # S9: a block that closes with too few dogs (a judge whose whole chain is small is exempt)
        T = p.short_block_dogs
        for j, segs in chains.items():
            if not free_of[j] or sum(e.entries for s in segs for e in s.events) < T:
                continue
            for q, seg in enumerate(segs, 1):
                for i, e in enumerate(seg.events):
                    if i < len(seg.events) - 1:
                        closes = self.c[id(seg.events[i + 1])]
                        conds = [] if closes is True else [closes]
                    elif len(segs) == 1:
                        closes, conds = True, []
                    else:
                        # closes when it is the judge's last event or the next segment starts a block
                        closes = md.NewBoolVar(f"closes_{id(e)}")
                        for (a, b), lit in self.arcs[j].items():
                            if a == q:
                                nc = True if b == 0 else self.c[id(segs[b - 1].events[0])]
                                md.AddImplication(lit, closes) if nc is True else md.AddBoolOr([lit.Not(), nc.Not(), closes])
                        conds = [closes]
                    if closes is False:
                        continue
                    sh = md.NewIntVar(0, T, f"short_{id(e)}")
                    md.Add(sh >= T - self.acc[id(e)]).OnlyEnforceIf(conds)
                    short_terms.append(sh)
                    flag = md.NewBoolVar(f"is_short_{id(e)}")
                    md.Add(sh <= T * flag)
                    self.short_flags.append(flag)

        # ring intervals: one per fixed-contiguous run of events (blocks in step 1)
        self.run_end: dict[int, cp_model.LinearExprT] = {}
        for ids, j in self.runs:
            first, last = ids[0], ids[-1]
            size = sum(self.d[u] for u in ids)
            for r in rings:
                ring_iv[r].append(md.NewOptionalFixedSizeIntervalVar(self.s[first], size, self.x[first][r],
                                                                     f"rv_{first}_{r.number}"))
            self.run_end[first] = self.s[last] + self.d[last]

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

        # ------------------------------------------------------------------ lunch (H6)
        l0, l1 = p.lunch_window[0] * SCALE, p.lunch_window[1] * SCALE
        lunch_len = p.lunch_min_minutes * SCALE
        self.lunch: dict[Judge, cp_model.IntVar] = {}
        self.before: dict[int, cp_model.IntVar] = {}  # event id → "judged before this judge's lunch"
        at_lunch: dict[int, cp_model.LinearExprT] = {}  # event id → "lunch comes just before it"
        for j in show.judges:
            work = sum(e.duration for e in show.events_for(j))
            if work <= p.lunch_required_after_minutes:
                continue
            segs = chains.get(j, [])
            events = [e for s in segs for e in s.events]
            pins = [sb for sb in show.pinned if sb.judge == j]
            lk = md.NewIntVar(-(-l0 // slot), l1 // slot, f"lunch_k_{j.name}")
            ls = md.NewIntVar(l0, l1, f"ls_{j.name}")
            md.Add(ls == slot * lk)
            judge_iv[j].append(md.NewFixedSizeIntervalVar(ls, lunch_len, f"lv_{j.name}"))
            self.lunch[j] = ls
            for e in events:
                u = id(e)
                b = self.before[u] = md.NewBoolVar(f"before_{u}")
                md.Add(self.s[u] + self.d[u] <= ls).OnlyEnforceIf(b)
                md.Add(self.s[u] >= ls + lunch_len).OnlyEnforceIf(b.Not())
            bef = self.before
            for e in events:
                u = id(e)
                if u in self.prev:
                    md.AddImplication(bef[u], bef[self.prev[u]])
                    at_lunch[u] = bef[self.prev[u]] - bef[u]
                    continue
                real = [(pu, a) for pu, a in self.preds[u] if pu is not None]
                if not real:
                    continue
                for pu, a in real:
                    md.AddBoolOr([a.Not(), bef[u].Not(), bef[pu]])
                bp = md.NewBoolVar(f"bp_{u}")  # the event before it is before lunch
                for pu, a in self.preds[u]:
                    md.Add(bp == (bef[u] if pu is None else bef[pu])).OnlyEnforceIf(a)
                at_lunch[u] = bp - bef[u]
            # judging both before and after the lunch (a break *in* the assignment)
            if not any(sb.end * SCALE <= l0 for sb in pins):
                if not events:
                    raise ValueError(f"{j.name} needs lunch but has no movable events before it")
                if len(segs) == 1:
                    md.Add(bef[id(events[0])] == 1)
                else:
                    md.AddBoolOr([bef[id(e)] for e in events])
            if not any(sb.start * SCALE >= l1 + lunch_len for sb in pins):
                if not events:
                    raise ValueError(f"{j.name} needs lunch but has no movable events after it")
                if len(segs) == 1:
                    md.Add(bef[id(events[-1])] == 0)
                else:
                    md.AddBoolOr([bef[id(e)].Not() for e in events])

        for r, ivs in ring_iv.items():
            md.AddNoOverlap(ivs)
        for j, ivs in judge_iv.items():
            md.AddNoOverlap(ivs)

        # ------------------------------------------------------------------ groups
        pinned_event_end = {id(e): _u(sb.start + off + e.duration)
                            for sb in show.pinned for e, off in sb.block.offsets()}

        def event_end(e):
            u = id(e)
            if u in self.s:
                return self.s[u] + self.d[u]
            return pinned_event_end[u]

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
            segs = chains.get(j, [])
            for q, seg in enumerate(segs, 1):
                last = id(seg.events[-1])
                is_last = self.arcs[j][q, 0]
                in_gr = sum(self.x[last][r] for r in group_rings) if group_rings else 0
                terms.append(event_end(seg.events[-1]) + travel - travel * in_gr - travel * (1 - is_last))
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
        if groups:  # the first group starts at the published group start
            md.AddMinEquality(self.first, [self.tau[g] for g in groups])
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
        for ids, _ in self.runs:
            first = ids[0]
            for r in group_rings:
                md.Add(self.run_end[first] + setup <= self.first).OnlyEnforceIf(self.x[first][r])
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

        # ------------------------------------------------------------------ soft costs of the structure
        # S2: a ring change costs `ring_change`, or `ring_change_at_lunch` when it happens at lunch
        chg_all, chg_off = [], []
        for j, u, chg in changes:
            chg_all.append(chg)
            if u in at_lunch:
                off = md.NewBoolVar(f"chg_off_{u}")
                md.Add(off >= chg - at_lunch[u])
                chg_off.append(off)
            else:
                chg_off.append(chg)
        # S10: lunch between two events of the same group
        splits = []
        event_of = {id(e): e for segs in chains.values() for s in segs for e in s.events}
        for u, al in at_lunch.items():
            g = event_of[u].group
            if g is None:
                continue
            if u in self.prev:
                if event_of[self.prev[u]].group == g:
                    splits.append(al)
                continue
            match = [a for pu, a in self.preds[u] if pu is not None and event_of[pu].group == g]
            if match:
                sp = md.NewBoolVar(f"split_{u}")
                md.Add(sp >= al + sum(match) - 1)
                splits.append(sp)
        blocks_expr = sum(1 if c is True else c for c in n_blocks if c is not False)
        equipment = self._equipment_switches(changes)

        # S5: a judge's day from first to last event, less their judging and lunch
        idle_terms = []
        for j, segs in chains.items():
            pins = [sb for sb in show.pinned if sb.judge == j]
            starts = [self.s[id(seg.events[0])] for seg in segs] + [sb.start * SCALE for sb in pins]
            ends = [self.s[id(seg.events[-1])] + self.d[id(seg.events[-1])] for seg in segs]
            ends += [sb.start * SCALE + _u(sb.block.duration) for sb in pins]
            if len(segs) == 1 and len(segs[0].events) == 1 and not pins:
                continue
            first_j = md.NewIntVar(t0, H, f"day_start_{j.name}")
            last_j = md.NewIntVar(t0, H, f"day_end_{j.name}")
            md.AddMinEquality(first_j, starts)
            md.AddMaxEquality(last_j, ends)
            busy = sum(self.d[id(e)] for seg in segs for e in seg.events) + sum(_u(sb.block.duration) for sb in pins)
            idle_terms.append(last_j - first_j - busy - (lunch_len if j in self.lunch else 0))
        idle_units = self.idle = sum(idle_terms)
        self.short_blocks = sum(self.short_flags)

        def iw(x: float) -> int:
            return int(round(x * WSCALE))

        self.cost_terms = {
            "ring_changes": sum(chg_all),
            "ring_changes_off_lunch": sum(chg_off),
            "short_block_dogs": sum(short_terms),
            "short_blocks": self.short_blocks,
            "lunch_splits": sum(splits),
            "blocks": blocks_expr,
            "equipment_switches": equipment,
            "judge_idle_units": idle_units,
        }
        self.cost_terms = {k: (v if not isinstance(v, int) else md.NewConstant(v)) for k, v in self.cost_terms.items()}
        self.structure = md.NewIntVar(0, 10**9, "structure")
        md.Add(self.structure == iw(w.ring_change_at_lunch) * sum(chg_all)
                          + iw(w.ring_change - w.ring_change_at_lunch) * sum(chg_off)
                          + iw(w.short_block) * sum(short_terms)
                          + iw(w.lunch_split) * sum(splits)
                          + iw(w.equipment_switch) * equipment
                          + iw(w.block_count) * blocks_expr)

    # ---------------------------------------------------------------------- S1 exhibitor conflicts
    def conflict_cost(self) -> cp_model.LinearExprT | None:
        """S1: tenths of a minute that registered exhibitors are needed in two rings at once.

        Built only when its phase runs, so the earlier phases do not carry these variables.
        ``None`` if no pair of events can conflict. A judge's own events cannot overlap, so
        those pairs are left out, and so are pairs of pinned events, which no decision can change.
        """
        md, H = self.model, self.H
        margin = _u(self.p.exhibitor_travel_minutes)
        index = {r: i for i, r in enumerate(dict.fromkeys(list(self.rings) + [sb.ring for sb in self.show.pinned]), 1)}
        pinned = {id(e): (round((sb.start + off) * SCALE), index[sb.ring])
                  for sb in self.show.pinned for e, off in sb.block.offsets()}

        def place(e: JudgingEvent):
            u = id(e)
            if u in self.s:
                return self.s[u], sum(index[r] * x for r, x in self.x[u].items())
            return pinned.get(u)

        terms = []
        for ea, eb, oa, ob, n in registered_window_pairs(self.show.entries):
            a, b = place(ea), place(eb)
            if a is None or b is None or ea.judge == eb.judge or (id(ea) in pinned and id(eb) in pinned):
                continue
            (sa, ra), (sb_, rb) = a, b
            la, ha = round(oa[0] * SCALE), round(oa[1] * SCALE)
            lb, hb = round(ob[0] * SCALE), round(ob[1] * SCALE)
            lo = md.NewIntVar(-margin, 2 * H, "conflict_lo")
            hi = md.NewIntVar(-margin, 2 * H, "conflict_hi")
            md.AddMaxEquality(lo, [sa + la - margin, sb_ + lb])
            md.AddMinEquality(hi, [sa + ha + margin, sb_ + hb])
            over = md.NewIntVar(0, min(ha - la + 2 * margin, hb - lb), "conflict_over")
            if margin:  # the travel margin applies between rings; one ring never overlaps itself
                same = md.NewBoolVar("conflict_same_ring")
                md.Add(ra == rb).OnlyEnforceIf(same)
                md.Add(over >= hi - lo).OnlyEnforceIf(same.Not())
            else:
                md.Add(over >= hi - lo)
            terms.append(n * over)
        if not terms:
            return None
        total = md.NewIntVar(0, 10**9, "conflict_total")
        md.Add(total == sum(terms))
        self.cost_terms["conflict_units"] = total
        return total

    # ---------------------------------------------------------------------- S3 equipment
    def _equipment_switches(self, changes):
        """S3: table/ramp/ground switches between consecutive breeds in a ring, free across a lunch
        (of the judge who leaves or arrives, in the ring's idle time, itself at least
        ``equipment_free_gap_minutes``). Counted within each segment
        and across the arcs between a judge's segments, and between judges at each judge's
        first and last breed (a judge who changes rings mid-day is not counted at that change,
        nor is a switch across a segment without breeds: approximations; the validator counts
        exactly)."""
        md, p, H, t0 = self.model, self.p, self.H, self.t0
        G = _u(p.equipment_free_gap_minutes)
        lunch_len = p.lunch_min_minutes * SCALE
        chg = {u: lit for _, u, lit in changes}
        terms: list = []

        def switch(ea: JudgingEvent, eb: JudgingEvent, path: list[JudgingEvent], arc=None):
            """Breed ``ea`` then breed ``eb`` with ``path`` judged between them (by the same judge)."""
            ua, ub = id(ea), id(eb)
            if arc is None and all(self.c[id(e)] is False for e in path + [eb]):
                terms.append(1)  # contiguous in one block: always a switch
                return
            between = sum(self.d[id(e)] for e in path)
            if ua in self.before and ub in self.before:  # free across the judge's lunch
                brk = md.NewBoolVar(f"eqbrk_{ua}_{ub}")
                md.Add(self.s[ub] - self.s[ua] - self.d[ua] - between >= G).OnlyEnforceIf(brk)
                md.AddImplication(brk, self.before[ua])
                md.AddImplication(brk, self.before[ub].Not())
            else:
                brk = 0
            moved = [chg[id(e)] for e in path + [eb] if id(e) in chg]
            sw = md.NewBoolVar(f"eqsw_{ua}_{ub}")
            md.Add(sw >= (1 if arc is None else arc) - brk - sum(moved))
            terms.append(sw)

        def ends(events):
            idx = [i for i, e in enumerate(events) if e.equipment is not None]
            return (idx[0], idx[-1]) if idx else (None, None)

        # within a judge: inside each segment, and across each arc between segments
        for j, segs in self.chains.items():
            for seg in segs:
                ev = list(seg.events)
                idx = [i for i, e in enumerate(ev) if e.equipment is not None]
                for a, b in zip(idx, idx[1:]):
                    if ev[a].equipment != ev[b].equipment:
                        switch(ev[a], ev[b], ev[a + 1:b])
            for (a, b), lit in self.arcs[j].items():
                if a == 0 or b == 0:
                    continue
                pa, pb = list(segs[a - 1].events), list(segs[b - 1].events)
                ia, ib = ends(pa)[1], ends(pb)[0]
                if ia is None or ib is None or pa[ia].equipment == pb[ib].equipment:
                    continue
                switch(pa[ia], pb[ib], pa[ia + 1:] + pb[:ib], arc=lit)

        # between judges: departures (a judge's last breed, a pinned block's last breed)
        # and arrivals (a judge's first breed, a pinned block's first breed)
        n = 0

        def merge(cands, name):
            """One (kinds, ring literals, start, end) from candidates (literal, kind, x, s, e)."""
            nonlocal n
            if len(cands) == 1 and cands[0][0] is True:
                _, kind, x, s, e = cands[0]
                return {kind: 1}, x, s, e
            n += 1
            kinds: dict[str, object] = defaultdict(int)
            sv = md.NewIntVar(t0, H, f"eq{name}s_{n}")
            evr = md.NewIntVar(t0, H, f"eq{name}e_{n}")
            xv = {r: md.NewBoolVar(f"eq{name}x_{n}_{r.number}") for r in self.rings}
            for lit, kind, x, s, e in cands:
                kinds[kind] = kinds[kind] + lit
                md.Add(sv == s).OnlyEnforceIf(lit)
                md.Add(evr == e).OnlyEnforceIf(lit)
                for r in self.rings:
                    md.Add(xv[r] == x[r]).OnlyEnforceIf(lit)
            return dict(kinds), xv, sv, evr

        deps, arrs = [], []  # (owner, kinds, ring literals {Ring: lit or 1}, start, end)
        for j, segs in self.chains.items():
            ac, dc = [], []
            for q, seg in enumerate(segs, 1):
                ev = list(seg.events)
                i0, i1 = ends(ev)
                if i0 is None:
                    continue
                f, l = ev[i0], ev[i1]
                uf, ul = id(f), id(l)
                ac.append((self.arcs[j][0, q], f.equipment, self.x[uf], self.s[uf], self.s[uf] + self.d[uf]))
                dc.append((self.arcs[j][q, 0], l.equipment, self.x[ul], self.s[ul], self.s[ul] + self.d[ul]))
            if ac:
                arrs.append((j, *merge(ac, "a")))
                deps.append((j, *merge(dc, "d")))
        for sb in self.show.pinned:
            ev = list(sb.block.events)
            i0, i1 = ends(ev)
            if i0 is None:
                continue
            first, last = ev[i0], ev[i1]
            offs = dict((id(e), o) for e, o in sb.block.offsets())
            ring = {sb.ring: 1}
            sf = _u(sb.start + offs[id(first)])
            sl = _u(sb.start + offs[id(last)])
            arrs.append((sb, {first.equipment: 1}, ring, sf, sf + _u(first.duration)))
            deps.append((sb, {last.equipment: 1}, ring, sl, sl + _u(last.duration)))
        for (od, kd, xd, sd, ed) in deps:
            for (oa, ka, xa, sa, ea) in arrs:
                if od is oa:
                    continue
                if isinstance(od, ScheduledBlock) and isinstance(oa, ScheduledBlock):
                    continue  # both pinned: fixed, not a decision
                fixed = all(isinstance(v, int) for v in list(kd.values()) + list(ka.values()))
                if fixed and len(kd) == 1 and kd.keys() == ka.keys():
                    continue
                common = [r for r in xd if r in xa]
                if not common:
                    continue
                n += 1
                if fixed and len(kd) == 1 and len(ka) == 1:
                    diff = 1
                else:
                    diff = md.NewBoolVar(f"eqdiff_{n}")  # the two breeds' equipment differ
                    for kind in set(kd) | set(ka):
                        md.Add(diff >= kd.get(kind, 0) - ka.get(kind, 0))
                # free across a lunch: the leaving or the arriving judge lunches in the ring's idle time
                lunches = []
                for owner in (od, oa):
                    jj = owner.judge if isinstance(owner, ScheduledBlock) else owner
                    ls = self.lunch.get(jj)
                    if ls is None:
                        continue
                    f = md.NewBoolVar(f"eqlunch_{n}_{len(lunches)}")
                    md.Add(ls >= ed).OnlyEnforceIf(f)
                    md.Add(ls + lunch_len <= sa).OnlyEnforceIf(f)
                    lunches.append(f)
                if lunches:
                    far = md.NewBoolVar(f"eqfar_{n}")
                    md.Add(sa >= ed + G).OnlyEnforceIf(far)
                    md.AddBoolOr(lunches).OnlyEnforceIf(far)
                else:
                    far = 0
                earlier = md.NewBoolVar(f"eqearly_{n}")  # arrival before the departing breed
                md.Add(sa + 1 <= sd).OnlyEnforceIf(earlier)
                h = md.NewBoolVar(f"eqh_{n}")
                for r in common:
                    md.Add(h >= xd[r] + xa[r] + diff - 2 - far - earlier)
                terms.append(h)
        return sum(terms) if terms else 0

    # ---------------------------------------------------------------------- hints
    def hint(self, prog: JudgingProgram) -> None:
        """Hint from a program judging the same events, each judge's in an order this model allows."""
        md, slot = self.model, self.slot
        where: dict[int, tuple[ScheduledBlock, float, bool]] = {}
        for sb in prog.blocks:
            for n, (e, off) in enumerate(sb.block.offsets()):
                where[id(e)] = (sb, off, n == 0)
        lunch = {br.judge: br.start * SCALE for br in prog.breaks if br.judge in self.lunch}
        for j, segs in self.chains.items():
            seg_of = {id(e): q for q, s in enumerate(segs, 1) for e in s.events}
            order = sorted((e for s in segs for e in s.events), key=lambda e: (where[id(e)][0].start, where[id(e)][1]))
            # arcs: the order in which the program visits the segments
            visits = [0]
            for e in order:
                if seg_of[id(e)] != visits[-1]:
                    visits.append(seg_of[id(e)])
            visits.append(0)
            used = set(zip(visits, visits[1:]))
            for key, lit in self.arcs[j].items():
                if lit is not True:
                    md.AddHint(lit, int(key in used))
            acc, bs, pos, prev_sb = 0, 0, 0, None
            for e in order:
                u = id(e)
                sb, off, first = where[u]
                s = pos = sb.start * SCALE if first else pos  # the model adds rounded durations
                if u in self.k:
                    md.AddHint(self.s[u], s)
                    if first:
                        md.AddHint(self.k[u], s // slot)
                    for r, x in self.x[u].items():
                        md.AddHint(x, int(sb.ring == r))
                if not isinstance(self.c[u], bool):
                    md.AddHint(self.c[u], int(first))
                if u in self.same:
                    md.AddHint(self.same[u], int(prev_sb is None or prev_sb.ring == sb.ring))
                if first:
                    acc, bs = 0, sb.start * SCALE
                acc += e.entries
                if u in self.bs:
                    md.AddHint(self.bs[u], bs)
                    md.AddHint(self.acc[u], acc)
                if u in self.before and j in lunch:
                    md.AddHint(self.before[u], int(sb.start * SCALE < lunch[j]))
                prev_sb = sb
                pos += self.d[u]
        for j, ls in lunch.items():
            md.AddHint(self.lunch[j], ls)

    def keep_solution_as_hint(self, solver: cp_model.CpSolver) -> None:
        md = self.model
        md.clear_hints()
        for idx in range(len(md.proto.variables)):
            var = md.get_int_var_from_proto_index(idx)
            md.add_hint(var, solver.value(var))

    # ---------------------------------------------------------------------- solution
    def order(self, j: Judge, solver: cp_model.CpSolver) -> Chain:
        """Judge ``j``'s events in the solution's running order."""
        segs = self.chains[j]
        nxt = {a: b for (a, b), lit in self.arcs[j].items() if lit is True or solver.Value(lit)}
        out: Chain = []
        q = nxt[0]
        while q != 0:
            out.extend(segs[q - 1].events)
            q = nxt[q]
        return out

    def extract(self, solver: cp_model.CpSolver) -> JudgingProgram:
        show = self.show
        out: list[ScheduledBlock] = list(show.pinned)
        for j in self.chains:
            chain = self.order(j, solver)
            current: list[JudgingEvent] = []
            head = None
            for e in chain + [None]:
                c = True if e is None else self.c[id(e)]
                starts = c if isinstance(c, bool) else bool(solver.Value(c))
                if starts and current:
                    ring = next(r for r, x in self.x[head].items() if solver.Value(x))
                    out.append(ScheduledBlock(Block(j, tuple(current)), ring, solver.Value(self.s[head]) // SCALE))
                    current = []
                if e is None:
                    break
                if starts:
                    head = id(e)
                current.append(e)
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
