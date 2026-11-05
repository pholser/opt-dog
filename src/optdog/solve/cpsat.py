"""CP-SAT scheduling model: placement of given blocks (step 1) and block formation (step 3a).

Each judge's events form a **chain** in a fixed running order. Between two consecutive
events a **cut** either starts a new block or continues the current one. In step 1
(``solve_placement``) every cut is fixed by the given blocks; in step 3a
(``solve_formation``) the solver chooses them. Either way it decides:

- where each judge's blocks begin (3a only), each block's **ring** and **start**,
- each lunch's **start**, for judges who need one,
- the **published group start** and the **group order**,

subject to every hard constraint of SPEC §4, minimizing **show finish** (BIS end).

Model (time in tenths of a minute, so 2.4 min/dog is exact; u⁻ is the event before u):

    c_u ∈ {0,1}: u starts a block (1 for the first event of a chain)
    c_u = 0 ⇒ s_u = s_u⁻ + d_u⁻, ring(u) = ring(u⁻)           blocks are contiguous (H5)
    c_u = 1 ⇒ s_u = Δ·k_u                                      published "not before" grid
    s_u ≥ e_u⁻ + m_J·[ring(u) ≠ ring(u⁻)]                      travel margin (H4)
    b_u = s_u if c_u else b_u⁻;  e_u − b_u ≤ cap               about an hour (H1; 3a only)
    ring r:  NoOverlap(optional intervals of events in r, pinned blocks in r)            (H3)
    judge j: NoOverlap(events, pinned ± travel, lunch)                                   (H4)
    lunch:   L0 ≤ ℓ_j ≤ L1, length = lunch min; judging before and after it              (H6)
    group g: τ_g ≥ end of every event feeding g                                          (H7)
             τ_g ≥ end of group judge's last event + m_J if it is outside the group rings (H8)
    first group start F (on the grid) ≤ τ_g for all g
             F ≥ end of every event placed in a group ring (+ set-up)                    (H9)
             F ≥ every event of ≥ 2 groups (chosen by the solver)                         (H13)
             F ≥ every judge's lunch end                                                 (H14)
    groups:  NoOverlap(group intervals); BIS starts after every group                    (H10)

H11 holds by the pinned blocks; H12 and V1 are input checks. Three lexicographic phases:
(1) minimize the BIS end; (2) at that finish, minimize the soft costs the block structure
controls (S2 ring changes, S9 short blocks, S10 lunch splitting a group, block count);
(3) at both, start blocks as early as possible. Each phase starts from the previous one's
solution; phase 1 starts from the greedy schedule (step 1) or the step-1 schedule (3a).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

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

SCALE = 10  # model time unit = 1/10 minute
WSCALE = 10  # soft-cost weights → integer coefficients

Chain = list[JudgingEvent]
Cut = bool | None  # event starts a block (True), continues the block (False), or the solver chooses (None)


def _u(minutes: float) -> int:
    """Minutes → model units, rounding durations up (never shorter than planned)."""
    return int(math.ceil(minutes * SCALE - 1e-6))


@dataclass(frozen=True)
class PlacementOptions:
    time_limit: float = 60.0  # seconds per phase
    workers: int = 8
    hint: bool = True  # start from a known schedule (greedy, or step 1 for formation)
    tidy: bool = True  # phases 2-3: same finish, lower soft costs, earlier blocks
    log: bool = False
    max_block_minutes: float = 65.0  # 3a: H1 cap on a formed block; same as the greedy (E: human p90 27 dogs)


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
    bound: float | None  # CP-SAT's proven lower bound on the finish for this model
    phases: list[PhaseStats] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)  # structure costs of the returned schedule

    @property
    def optimal(self) -> bool:
        return bool(self.phases) and self.phases[0].status == "OPTIMAL"


# --------------------------------------------------------------------------- chains


def blocks_from_program(program: JudgingProgram) -> dict[Judge, list[Block]]:
    """Each judge's unpinned blocks, in the order they are judged in ``program``."""
    pinned = {id(sb) for sb in program.show.pinned}
    out: dict[Judge, list[Block]] = defaultdict(list)
    for sb in sorted(program.blocks, key=lambda b: b.start):
        if id(sb) not in pinned:
            out[sb.judge].append(sb.block)
    return dict(out)


def chains_from_blocks(blocks: dict[Judge, list[Block]]) -> dict[Judge, tuple[Chain, list[Cut]]]:
    """The judges' chains with every cut fixed by ``blocks`` (step 1)."""
    out = {}
    for j, bl in blocks.items():
        chain = [e for b in bl for e in b.events]
        cuts: list[Cut] = [n == 0 for b in bl for n in range(len(b.events))]
        out[j] = (chain, cuts)
    return out


def free_cuts(chains: dict[Judge, Chain], cap_minutes: float) -> dict[Judge, tuple[Chain, list[Cut]]]:
    """The judges' chains with cuts for the solver to choose (step 3a), except that

    - an event longer than the block cap is a block of its own (H1 exception), and
    - a sweepstakes stays in the same block as the breed it precedes (H15), unless that
      breed must be alone.
    """
    out = {}
    for j, chain in chains.items():
        cuts: list[Cut] = [None] * len(chain)
        cuts[0] = True
        alone = [e.duration > cap_minutes for e in chain]
        for i in range(1, len(chain)):
            a, b = chain[i - 1], chain[i]
            if alone[i - 1] or alone[i]:
                cuts[i] = True
            elif a.kind is EventKind.SWEEPSTAKES and b.kind is EventKind.BREED and a.breed == b.breed:
                cuts[i] = False
        out[j] = (chain, cuts)
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


def solve_formation(show: Show, params: ShowParameters | None = None,
                    options: PlacementOptions | None = None) -> PlacementResult:
    """Step 3a: keep each judge's running order (the greedy's), let the solver choose where
    blocks break, where lunch falls, and each block's ring and start."""
    p = (params or ShowParameters()).for_show(show)
    o = options or PlacementOptions()
    greedy = greedy_program(show, p)
    blocks = blocks_from_program(greedy)
    start = solve_placement(show, p, blocks, replace(o, tidy=False))
    chains = {j: [e for b in bl for e in b.events] for j, bl in blocks.items()}
    m = _Model(show, p, free_cuts(chains, o.max_block_minutes), cap=o.max_block_minutes)
    return _solve(m, o, start.program or greedy)


def _solve(m: "_Model", o: PlacementOptions, hint_prog: JudgingProgram | None) -> PlacementResult:
    if o.hint and hint_prog is not None:
        m.hint(hint_prog)
    res = PlacementResult(None, None, None)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = o.workers
    solver.parameters.max_time_in_seconds = o.time_limit
    solver.parameters.log_search_progress = o.log
    ok = (cp_model.OPTIMAL, cp_model.FEASIBLE)

    def run(name: str, objective, scale: float) -> bool:
        m.model.Minimize(objective)
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
    res.finish = best_finish / SCALE
    res.bound = solver.BestObjectiveBound() / SCALE
    m.model.Add(m.finish <= best_finish)

    if o.tidy:
        # phase 2: soft costs of the block structure, at that finish
        if run("structure", m.structure, WSCALE):
            m.model.Add(m.structure <= solver.Value(m.structure))
            # phase 3: earliest blocks
            run("times", sum(m.run_starts) + m.first, SCALE)
        if res.phases[-1].status not in ("OPTIMAL", "FEASIBLE"):
            m.model.Minimize(m.finish)  # fall back to the last good solution (kept as the hint)
            solver.Solve(m.model)
    res.program = m.extract(solver)
    res.costs = {k: float(solver.Value(v)) for k, v in m.cost_terms.items()}
    return res


# --------------------------------------------------------------------------- model


class _Model:
    def __init__(self, show: Show, p: ShowParameters, chains: dict[Judge, tuple[Chain, list[Cut]]],
                 cap: float | None):
        self.show, self.p, self.chains = show, p, chains
        md = self.model = cp_model.CpModel()
        slot = self.slot = p.slot_minutes * SCALE
        t0 = show.start * SCALE
        H = t0 + 18 * 60 * SCALE
        travel = _u(p.judge_travel_minutes)
        rings = self.rings = list(show.rings)
        group_rings = set(show.group_rings)
        w = p.weights

        # ------------------------------------------------------------------ events
        # per event id: start s (var or var + const), cut c (bool or literal), ring literals x
        self.s: dict[int, cp_model.LinearExprT] = {}
        self.d: dict[int, int] = {}
        self.c: dict[int, object] = {}
        self.k: dict[int, cp_model.IntVar] = {}
        self.x: dict[int, dict[Ring, cp_model.IntVar]] = {}
        self.same: dict[int, cp_model.IntVar] = {}  # event id → literal "same ring as the event before"
        self.bs: dict[int, cp_model.IntVar] = {}  # block start (3a)
        self.acc: dict[int, cp_model.IntVar] = {}  # dogs so far in the block (3a)
        self.run_starts: list = []
        ring_iv: dict[Ring, list] = defaultdict(list)
        judge_iv: dict[Judge, list] = defaultdict(list)
        self.runs: list[tuple[list[int], object]] = []  # (event ids, ...) of each fixed-contiguous run
        changes, short_terms = [], []
        n_blocks = []

        for j, (chain, cuts) in chains.items():
            free = any(c is None for c in cuts)
            total_dogs = sum(e.entries for e in chain)
            run: list[int] = []
            for i, (e, cut) in enumerate(zip(chain, cuts)):
                u = id(e)
                d = self.d[u] = _u(e.duration)
                prev = id(chain[i - 1]) if i else None
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
                        changes.append((j, i, same.Not()))
                    # a new fixed-contiguous run starts here
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
                    starts_here = [] if c is False else ([] if c is True else [c])
                    if c is not False:
                        md.Add(bs == self.s[u]).OnlyEnforceIf(starts_here)
                        md.Add(acc == e.entries).OnlyEnforceIf(starts_here)
                    if c is not True:
                        cont = [] if c is False else [c.Not()]
                        md.Add(bs == self.bs[prev]).OnlyEnforceIf(cont)
                        md.Add(acc == self.acc[prev] + e.entries).OnlyEnforceIf(cont)
                    if cap is not None and e.duration <= cap:
                        md.Add(self.s[u] + d - bs <= _u(cap))
            if run:
                self.runs.append((run, j))

            # S9: a block that closes with too few dogs (a judge whose whole chain is small is exempt)
            T = p.short_block_dogs
            if free and total_dogs >= T:
                for i, e in enumerate(chain):
                    closes = True if i == len(chain) - 1 else self.c[id(chain[i + 1])]
                    if closes is False:
                        continue
                    sh = md.NewIntVar(0, T, f"short_{id(e)}")
                    md.Add(sh >= T - self.acc[id(e)]).OnlyEnforceIf([] if closes is True else [closes])
                    short_terms.append(sh)

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
        at_lunch: dict[tuple[Judge, int], cp_model.LinearExprT] = {}
        for j in show.judges:
            work = sum(e.duration for e in show.events_for(j))
            if work <= p.lunch_required_after_minutes:
                continue
            chain = chains.get(j, ([], []))[0]
            pins = [sb for sb in show.pinned if sb.judge == j]
            lk = md.NewIntVar(-(-l0 // slot), l1 // slot, f"lunch_k_{j.name}")
            ls = md.NewIntVar(l0, l1, f"ls_{j.name}")
            md.Add(ls == slot * lk)
            judge_iv[j].append(md.NewFixedSizeIntervalVar(ls, lunch_len, f"lv_{j.name}"))
            self.lunch[j] = ls
            bef = []
            for e in chain:
                u = id(e)
                b = self.before[u] = md.NewBoolVar(f"before_{u}")
                md.Add(self.s[u] + self.d[u] <= ls).OnlyEnforceIf(b)
                md.Add(self.s[u] >= ls + lunch_len).OnlyEnforceIf(b.Not())
                bef.append(b)
            for a, b in zip(bef, bef[1:]):
                md.AddImplication(b, a)
            for i in range(1, len(chain)):
                at_lunch[j, i] = bef[i - 1] - bef[i]
            # judging both before and after the lunch (a break *in* the assignment)
            if not any(sb.end * SCALE <= l0 for sb in pins):
                if not chain:
                    raise ValueError(f"{j.name} needs lunch but has no movable events before it")
                md.Add(bef[0] == 1)
            if not any(sb.start * SCALE >= l1 + lunch_len for sb in pins):
                if not chain:
                    raise ValueError(f"{j.name} needs lunch but has no movable events after it")
                md.Add(bef[-1] == 0)

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
            chain = chains.get(j, ([], []))[0]
            if chain:
                last = id(chain[-1])
                in_gr = sum(self.x[last][r] for r in group_rings) if group_rings else 0
                terms.append(event_end(chain[-1]) + travel - travel * in_gr)
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
        for j, i, chg in changes:
            chg_all.append(chg)
            if (j, i) in at_lunch:
                off = md.NewBoolVar(f"chg_off_{j.name}_{i}")
                md.Add(off >= chg - at_lunch[j, i])
                chg_off.append(off)
            else:
                chg_off.append(chg)
        # S10: lunch between two events of the same group
        splits = []
        for (j, i), al in at_lunch.items():
            chain = chains[j][0]
            g0, g1 = chain[i - 1].group, chain[i].group
            if g0 is not None and g0 == g1:
                splits.append(al)
        blocks_expr = sum(1 if c is True else c for c in n_blocks if c is not False)

        def iw(x: float) -> int:
            return int(round(x * WSCALE))

        self.cost_terms = {
            "ring_changes": sum(chg_all),
            "ring_changes_off_lunch": sum(chg_off),
            "short_block_dogs": sum(short_terms),
            "lunch_splits": sum(splits),
            "blocks": blocks_expr,
        }
        self.cost_terms = {k: (v if not isinstance(v, int) else md.NewConstant(v)) for k, v in self.cost_terms.items()}
        self.structure = md.NewIntVar(0, 10**9, "structure")
        md.Add(self.structure == iw(w.ring_change_at_lunch) * sum(chg_all)
                          + iw(w.ring_change - w.ring_change_at_lunch) * sum(chg_off)
                          + iw(w.short_block) * sum(short_terms)
                          + iw(w.lunch_split) * sum(splits)
                          + iw(w.block_count) * blocks_expr)

    # ---------------------------------------------------------------------- hints
    def hint(self, prog: JudgingProgram) -> None:
        """Hint from a program whose blocks split the same chains, in the same order."""
        md, slot = self.model, self.slot
        where: dict[int, tuple[ScheduledBlock, float, bool]] = {}
        for sb in prog.blocks:
            for n, (e, off) in enumerate(sb.block.offsets()):
                where[id(e)] = (sb, off, n == 0)
        lunch = {br.judge: br.start * SCALE for br in prog.breaks if br.judge in self.lunch}
        for j, (chain, _) in self.chains.items():
            acc, bs, pos, prev_sb = 0, 0, 0, None
            for e in chain:
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
                    md.AddHint(self.same[u], int(prev_sb is not None and prev_sb.ring == sb.ring))
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
    def extract(self, solver: cp_model.CpSolver) -> JudgingProgram:
        show = self.show
        out: list[ScheduledBlock] = list(show.pinned)
        for j, (chain, _) in self.chains.items():
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
