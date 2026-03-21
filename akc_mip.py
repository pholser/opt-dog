"""
akc_mip.py — AKC All-Breed Show Scheduling MIP Model
See MODEL_SPEC.md for the complete mathematical specification.

Architecture
------------
  solve_show(show, params)          Public entry point
  _build_model(show, params)        Construct Pyomo model (MODEL_SPEC §3)
  _compute_greedy_warmstart(model)  Build greedy solution as {VarData: value}
  _solve_scip(model, ws, params)    SCIP backend (via pyscipopt)
  _solve_pyomo(model, ws, params)   HiGHS / CBC backend (via Pyomo SolverFactory)
  _build_lp_var_map(model, path)    LP-file name → Pyomo VarData (SCIP only)
  _extract_result(...)              Build SolveResult from solved model

Supported solvers (SolveParams.solver):
  "scip"   — pyscipopt (default; best performance; warm-start via native API)
  "highs"  — HiGHS via Pyomo SolverFactory ("appsi_highs" or "highs")
  "cbc"    — CBC via Pyomo SolverFactory ("cbc")
  "glpk"   — GLPK via Pyomo SolverFactory ("glpk") — slow, for small tests only
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class SolveParams:
    """Solver configuration.  All times in minutes unless noted."""
    solver:            str   = "scip"    # "scip" | "highs" | "cbc" | "glpk"
    time_limit_sec:    int   = 300
    mip_gap:           float = 0.01      # 1 % optimality gap target
    threads:           int   = 0         # 0 = solver default
    tee:               bool  = False     # stream solver output to stdout

    # Objective weights — set automatically in _build_model
    w1_bis_start:      float = 0.0
    w3_friction:       float = 1.0

    def slots(self, minutes: float) -> int:
        """Convert minutes to slots (rounds up)."""
        return math.ceil(minutes / (self._slot_min if hasattr(self, '_slot_min') else 5))


@dataclass
class SegmentSchedule:
    segment_id:   str
    judge_id:     str
    ring_id:      str
    start_slot:   int       # absolute slot
    end_slot:     int       # absolute slot (exclusive)
    n_dogs:       int
    breed_ids:    List[str]


@dataclass
class GroupSchedule:
    group_id:   str
    judge_id:   str
    ring_id:    str
    start_slot: int
    end_slot:   int
    n_breeds:   int


@dataclass
class SolveResult:
    status:          str            # 'OPTIMAL' | 'FEASIBLE' | 'INFEASIBLE' | 'ERROR'
    mip_gap:         float
    solve_time_sec:  float
    bis_start_slot:  int            # absolute
    segments:        List[SegmentSchedule] = field(default_factory=list)
    groups:          List[GroupSchedule]   = field(default_factory=list)
    lunch_slots:     Dict[str, int]        = field(default_factory=dict)  # jid → abs slot
    equip_switches:  int = 0
    ring_switches:   int = 0
    n_conflicts:     int = 0

    def summary(self) -> str:
        return (
            f"status={self.status}  gap={self.mip_gap*100:.1f}%  "
            f"time={self.solve_time_sec:.0f}s  "
            f"BIS_slot={self.bis_start_slot}  "
            f"ring_sw={self.ring_switches}  equip_sw={self.equip_switches}"
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def solve_show(show, params: Optional[SolveParams] = None) -> SolveResult:
    """Build and solve the MIP for *show*.  Returns a SolveResult.

    The solver is selected by params.solver:
      "scip"  — pyscipopt (default; best performance)
      "highs" — HiGHS via Pyomo SolverFactory
      "cbc"   — CBC via Pyomo SolverFactory
      "glpk"  — GLPK via Pyomo SolverFactory (slow, small tests only)
    """
    if params is None:
        params = SolveParams()

    t0    = time.time()
    model = _build_model(show, params)

    # Compute greedy warm-start as {VarData: float} — solver-independent
    warmstart: Dict = {}
    try:
        warmstart = _compute_greedy_warmstart(model, show)
        log.info("Greedy warm-start computed: %d variable assignments", len(warmstart))
    except Exception as e:
        log.warning("Greedy warm-start failed (continuing without): %s", e)

    solver = params.solver.lower()
    if solver == "scip":
        result = _solve_scip(model, warmstart, show, params, t0)
    else:
        result = _solve_pyomo(model, warmstart, show, params, t0)

    return result


# ---------------------------------------------------------------------------
# SCIP backend
# ---------------------------------------------------------------------------

def _solve_scip(model, warmstart: Dict, show, params: SolveParams, t0: float) -> SolveResult:
    """Solve using pyscipopt directly (best warm-start support, best performance)."""
    import pyscipopt

    scip = pyscipopt.Model()
    scip.hideOutput(not params.tee)
    scip.setParam("limits/gap",  params.mip_gap)
    scip.setParam("limits/time", params.time_limit_sec)
    if params.threads:
        scip.setParam("parallel/maxnthreads", params.threads)

    with tempfile.NamedTemporaryFile(suffix=".lp", delete=False) as f:
        lp_path = f.name
    try:
        model.write(lp_path, format="lp",
                    io_options={"symbolic_solver_labels": True})
        scip.readProblem(lp_path)
        lp_var_map = _build_lp_var_map(model, lp_path)
        log.info("LP var map: %d Pyomo vars matched", len(lp_var_map))

        if warmstart:
            _inject_scip_warmstart(scip, lp_var_map, warmstart)

        scip.optimize()
        result = _extract_result_scip(model, scip, lp_var_map, show, params,
                                      time.time() - t0)
    finally:
        try:
            os.unlink(lp_path)
        except OSError:
            pass

    return result


def _inject_scip_warmstart(scip, lp_var_map: Dict, warmstart: list):
    """Apply a [(VarData, value)] warm-start list to a pyscipopt Model."""
    import pyscipopt

    # Reverse map: id(VarData) → lp_name
    id_to_lp = {id(vd): lp_name for lp_name, vd in lp_var_map.items()}
    scip_vars = {v.name: v for v in scip.getVars()}

    sol       = scip.createSol()
    _set_names: set = set()

    for vd, val in warmstart:
        lp_name = id_to_lp.get(id(vd))
        if lp_name is None:
            continue
        sv = scip_vars.get(lp_name)
        if sv is None:
            continue
        scip.setSolVal(sol, sv, float(val))
        _set_names.add(lp_name)

    # lb-fill: set any uninitialized SCIP var with lb > 0 to its lower bound
    # (catches ONE_VAR_CONSTANT and any variables missed above)
    for sv in scip.getVars():
        if sv.name in _set_names:
            continue
        lb = sv.getLbOriginal()
        if lb > 0:
            scip.setSolVal(sol, sv, lb)

    accepted = scip.addSol(sol)
    log.info("SCIP warm-start %s", "accepted" if accepted else "rejected")


# ---------------------------------------------------------------------------
# HiGHS / CBC / GLPK backend (Pyomo SolverFactory)
# ---------------------------------------------------------------------------

# Candidate Pyomo solver names tried in order for each solver key.
# "appsi_highs" uses the faster APPSI interface; plain "highs" is the fallback.
_PYOMO_SOLVER_NAMES = {
    "highs": ["appsi_highs", "highs"],
    "cbc":   ["cbc"],
    "glpk":  ["glpk"],
}

# Per-solver option keys: (time_limit_key, mip_gap_key, threads_key | None)
_SOLVER_OPTION_KEYS = {
    "appsi_highs": ("time_limit",  "mip_rel_gap", "threads"),
    "highs":       ("time_limit",  "mip_rel_gap", "threads"),
    "cbc":         ("sec",         "ratio",        None),   # threads via separate flag
    "glpk":        ("tmlim",       "mipgap",       None),
}


def _find_pyomo_solver(solver_key: str):
    """Return (solver_obj, used_name) or raise RuntimeError with a helpful message."""
    import pyomo.environ as pyo

    candidates = _PYOMO_SOLVER_NAMES.get(solver_key, [solver_key])
    for name in candidates:
        try:
            s = pyo.SolverFactory(name)
            if s.available(exception_flag=False):
                log.info("Solver '%s' available via Pyomo SolverFactory", name)
                return s, name
        except Exception as exc:
            log.debug("Solver candidate '%s' unavailable: %s", name, exc)

    raise RuntimeError(
        f"No available Pyomo solver found for '{solver_key}'. "
        f"Tried: {candidates}. "
        f"For HiGHS: pip install highspy. "
        f"For CBC: install coin-or-cbc from conda-forge or your OS packages.")


def _solve_pyomo(model, warmstart: Dict, show, params: SolveParams, t0: float) -> SolveResult:
    """
    Solve using Pyomo SolverFactory (HiGHS, CBC, GLPK).

    Warm-start: Pyomo variable values are set from the warmstart dict, then
    warmstart=True is passed to the solver if it declares warm_start_capable().
    HiGHS (appsi_highs) and CBC both accept MIP starts this way.
    GLPK ignores warm-starts.
    """
    solver_obj, used_name = _find_pyomo_solver(params.solver.lower())

    # Set Pyomo variable values for warm-start
    if warmstart:
        n_set = 0
        for vd, val in warmstart:
            try:
                vd.set_value(val)
                n_set += 1
            except Exception:
                pass
        log.info("Warm-start: set %d/%d variable values for %s",
                 n_set, len(warmstart), used_name)

    # Build solver options dict
    tl_key, gap_key, thr_key = _SOLVER_OPTION_KEYS.get(
        used_name, ("time_limit", "mip_rel_gap", None))

    options: Dict = {}
    if params.time_limit_sec and tl_key:
        options[tl_key] = params.time_limit_sec
    if params.mip_gap and gap_key:
        options[gap_key] = params.mip_gap
    if params.threads and thr_key:
        options[thr_key] = params.threads
    # CBC: threads as a separate options key (different from APPSI path)
    if used_name == "cbc" and params.threads:
        options["threads"] = params.threads

    log.info("Calling %s with options=%s", used_name, options)

    ws_capable = bool(warmstart) and getattr(
        solver_obj, "warm_start_capable", lambda: False)()

    # Some older Pyomo/solver combos raise TypeError on unknown kwargs
    try:
        results = solver_obj.solve(
            model, tee=params.tee, options=options,
            warmstart=ws_capable, load_solutions=True)
    except TypeError:
        results = solver_obj.solve(
            model, tee=params.tee, options=options,
            load_solutions=True)

    elapsed = time.time() - t0
    return _extract_result_pyomo(model, results, show, params, elapsed)


# ---------------------------------------------------------------------------
# Result extraction — SCIP
# ---------------------------------------------------------------------------

def _extract_result_scip(model, scip, lp_var_map, show, params, elapsed) -> SolveResult:
    """Load SCIP solution into Pyomo, then build SolveResult."""
    import pyscipopt

    status_map = {
        pyscipopt.SCIP_STATUS.OPTIMAL:    "OPTIMAL",
        pyscipopt.SCIP_STATUS.TIMELIMIT:  "FEASIBLE",
        pyscipopt.SCIP_STATUS.INFEASIBLE: "INFEASIBLE",
        pyscipopt.SCIP_STATUS.UNBOUNDED:  "ERROR",
    }
    scip_status = scip.getStatus()
    status = status_map.get(scip_status, "FEASIBLE")
    gap    = scip.getGap() if scip.getNSols() > 0 else 1.0

    if scip.getNSols() == 0:
        log.error("No solution found (status=%s)", scip_status)
        return SolveResult(status="INFEASIBLE", mip_gap=1.0,
                           solve_time_sec=elapsed, bis_start_slot=0)

    scip_var_map  = {v.name: v for v in scip.getVars()}
    id_to_scip    = {id(vd): scip_var_map[lp_name]
                     for lp_name, vd in lp_var_map.items()
                     if lp_name in scip_var_map}
    best_sol      = scip.getBestSol()

    def safe_val(pyomo_var):
        sv = id_to_scip.get(id(pyomo_var))
        if sv is None:
            return 0.0
        try:
            return scip.getSolVal(best_sol, sv)
        except Exception:
            return 0.0

    return _build_solve_result(model, show, safe_val, status, gap, elapsed)


# ---------------------------------------------------------------------------
# Result extraction — Pyomo SolverFactory
# ---------------------------------------------------------------------------

def _extract_result_pyomo(model, solver_results, show, params, elapsed) -> SolveResult:
    """Build SolveResult from a Pyomo solver results object (load_solutions=True)."""
    import pyomo.environ as pyo
    from pyomo.opt import SolverStatus, TerminationCondition as TC

    sol_status = solver_results.solver.status
    term_cond  = solver_results.solver.termination_condition

    if sol_status == SolverStatus.ok and term_cond == TC.optimal:
        status = "OPTIMAL"
    elif term_cond in (TC.maxTimeLimit, TC.maxIterations, TC.other):
        status = "FEASIBLE"
    elif term_cond == TC.infeasible:
        status = "INFEASIBLE"
    else:
        # Treat aborted / unknown as feasible if any solution was loaded
        status = "FEASIBLE"

    # Extract MIP gap — try several attribute paths used by different solvers:
    #   Pyomo standard:    problem.upper_bound / problem.lower_bound
    #   APPSI HiGHS attr:  solver_results.solver.mip_gap (if present)
    gap = 1.0
    try:
        # APPSI HiGHS sometimes sets this directly
        raw_gap = getattr(solver_results.solver, "mip_gap", None)
        if raw_gap is not None:
            gap = float(raw_gap)
        else:
            ub = float(solver_results.problem.upper_bound)
            lb = float(solver_results.problem.lower_bound)
            if abs(ub) > 1e-10:
                gap = max(0.0, (ub - lb) / abs(ub))
    except Exception:
        pass

    log.info("Pyomo solver: status=%s term=%s gap=%.3f", sol_status, term_cond, gap)

    if status == "INFEASIBLE":
        log.error("No solution found (termination=%s)", term_cond)
        return SolveResult(status="INFEASIBLE", mip_gap=1.0,
                           solve_time_sec=elapsed, bis_start_slot=0)

    def safe_val(pyomo_var):
        try:
            v = pyo.value(pyomo_var)
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    return _build_solve_result(model, show, safe_val, status, gap, elapsed)


# ---------------------------------------------------------------------------
# Common result builder
# ---------------------------------------------------------------------------

def _build_solve_result(model, show, safe_val, status, gap, elapsed) -> SolveResult:
    """Construct SolveResult given a safe_val(VarData) → float function."""
    T0         = model._T0
    seg_map    = model._seg_map
    ring_ids   = list(show.rings.keys())

    seg_schedules = []
    for s in show.segments:
        sid     = s.segment_id
        t_start = int(round(safe_val(model.start_s[sid])))
        ring    = next((r for r in ring_ids if safe_val(model.u[sid, r]) > 0.5),
                       ring_ids[0])
        seg_schedules.append(SegmentSchedule(
            segment_id=sid,
            judge_id=s.judge_id,
            ring_id=ring,
            start_slot=T0 + t_start,
            end_slot=T0 + t_start + s.duration_slots,
            n_dogs=s.n_dogs,
            breed_ids=s.breed_ids,
        ))

    grp_schedules = []
    arena_label   = "+".join(sorted(show.group_rings))
    for gid in show.groups:
        grp = show.groups[gid]
        t_g = int(round(safe_val(model.tau_g[gid])))
        dur = model._grp_dur[gid]
        grp_schedules.append(GroupSchedule(
            group_id=gid,
            judge_id=grp.judge_id,
            ring_id=arena_label,
            start_slot=T0 + t_g,
            end_slot=T0 + t_g + dur,
            n_breeds=len(grp.breed_ids) if hasattr(grp, 'breed_ids') else 0,
        ))

    bis_start   = T0 + int(round(safe_val(model.tau_bis)))
    lunch_slots = {jid: T0 + int(round(safe_val(model.ell[jid])))
                   for jid in model._lunch_judges}
    rs          = sum(1 for j, i in model._rs_pairs
                      if safe_val(model.z[j, i]) > 0.5)
    eq          = sum(1 for s in show.segments if s.has_equipment_mix)

    p = show.params
    log.info("Solve result: %s  gap=%.2f%%  time=%.1fs  BIS=%s",
             status, gap * 100, elapsed, p.slot_to_hhmm(bis_start))
    log.info("  Ring switches: %d  Equip switches: %d", rs, eq)

    return SolveResult(
        status=status,
        mip_gap=gap,
        solve_time_sec=elapsed,
        bis_start_slot=bis_start,
        segments=seg_schedules,
        groups=sorted(grp_schedules, key=lambda g: g.start_slot),
        lunch_slots=lunch_slots,
        equip_switches=eq,
        ring_switches=rs,
        n_conflicts=0,
    )


# ---------------------------------------------------------------------------
# Model builder
# ---------------------------------------------------------------------------

def _build_model(show, params: SolveParams):
    """Construct Pyomo ConcreteModel per MODEL_SPEC §3."""
    import pyomo.environ as pyo

    m  = pyo.ConcreteModel(name="AKC_Show_Schedule")
    p  = show.params
    T  = p.total_slots
    T0 = p.judging_start_slot
    params._slot_min = p.slot_minutes   # stash for SolveParams.slots()

    segs      = show.segments
    seg_ids   = [s.segment_id for s in segs]
    seg_map   = {s.segment_id: s for s in segs}

    ring_ids  = list(show.rings.keys())
    grp_rings = show.group_rings            # list of ring IDs used for groups/BIS

    groups    = show.groups
    group_ids = list(groups.keys())
    judge_ids = list(show.judges.keys())

    # Segments per judge (preprocessing order preserved)
    judge_segs: Dict[str, List] = {jid: [] for jid in judge_ids}
    for s in segs:
        judge_segs[s.judge_id].append(s)

    # -------------------------------------------------------------------
    # Index sets (MODEL_SPEC §3.1)
    # -------------------------------------------------------------------

    m.SEGS   = pyo.Set(initialize=seg_ids)
    m.RINGS  = pyo.Set(initialize=ring_ids)
    m.GROUPS = pyo.Set(initialize=group_ids)

    # Same-judge pairs: (sa, sb) with sa earlier in judge's list
    same_judge_pairs = []
    for jid, jsegs in judge_segs.items():
        for i, sa in enumerate(jsegs):
            for sb in jsegs[i + 1:]:
                same_judge_pairs.append((sa.segment_id, sb.segment_id))
    m.SEG_PAIRS = pyo.Set(initialize=same_judge_pairs, dimen=2)

    sj_set = set(same_judge_pairs)
    # Cross-judge pairs (for ring non-overlap); exclude same-judge (already handled by C5)
    cross_pairs = [(seg_ids[i], seg_ids[j])
                   for i in range(len(seg_ids))
                   for j in range(i + 1, len(seg_ids))
                   if (seg_ids[i], seg_ids[j]) not in sj_set]
    m.SEG_PAIRS_ALL = pyo.Set(initialize=cross_pairs, dimen=2)

    # Group-group arena pairs (ordered canonical pairs)
    gg_pairs = [(group_ids[i], group_ids[j])
                for i in range(len(group_ids))
                for j in range(i + 1, len(group_ids))]
    m.GG_PAIRS = pyo.Set(initialize=gg_pairs, dimen=2)

    # Lunch sets
    lunch_judges = show.judges_requiring_lunch
    soft_lunch_judges = [jid for jid in judge_ids
                         if jid not in lunch_judges
                         and len(judge_segs.get(jid, [])) >= 2]

    t_ls = p.lunch_start_slot - T0
    t_le = p.lunch_end_slot   - T0
    L    = p.lunch_duration_slots

    lunch_gap_pairs = [(jid, i)
                       for jid in lunch_judges
                       for i in range(len(judge_segs.get(jid, [])) - 1)]
    m.LUNCH_GAPS = pyo.Set(initialize=lunch_gap_pairs, dimen=2)

    sl_gap_pairs = [(jid, i)
                    for jid in soft_lunch_judges
                    for i in range(len(judge_segs[jid]) - 1)]
    m.SL_GAPS     = pyo.Set(initialize=sl_gap_pairs, dimen=2)
    m.SOFT_LUNCH_J = pyo.Set(initialize=soft_lunch_judges)

    # Ring-switch pairs
    rs_pairs = [(jid, i)
                for jid in judge_ids
                for i in range(len(judge_segs.get(jid, [])) - 1)]
    m.RS_PAIRS = pyo.Set(initialize=rs_pairs, dimen=2)

    # Judge-group-segment triples
    grp_judge: Dict[str, str] = {gid: show.groups[gid].judge_id for gid in group_ids}
    jg_seg_triples = []
    for gid in group_ids:
        jid = grp_judge[gid]
        for seg in judge_segs.get(jid, []):
            jg_seg_triples.append((jid, gid, seg.segment_id))
    m.JG_SEG = pyo.Set(initialize=jg_seg_triples, dimen=3)

    # BIS judge breed segments
    bis_jid = show.bis_judge_id
    bis_segs = [s.segment_id for s in judge_segs.get(bis_jid, [])]
    m.BIS_SEGS = pyo.Set(initialize=bis_segs)

    # -------------------------------------------------------------------
    # Parameters (MODEL_SPEC §3.2)
    # -------------------------------------------------------------------

    m.D     = pyo.Param(m.SEGS,   initialize={s.segment_id: s.duration_slots for s in segs})
    m.Dg    = pyo.Param(m.GROUPS, initialize={gid: groups[gid].judging_duration_slots
                                               for gid in group_ids})
    D_BIS   = math.ceil(20 / p.slot_minutes)   # 20 min BIS, fixed
    m.D_BIS = pyo.Param(initialize=D_BIS)

    # -------------------------------------------------------------------
    # Decision Variables (MODEL_SPEC §3.3)
    # -------------------------------------------------------------------

    m.u       = pyo.Var(m.SEGS, m.RINGS, domain=pyo.Binary)
    m.start_s = pyo.Var(m.SEGS, domain=pyo.NonNegativeReals, bounds=(0, T))
    m.ord     = pyo.Var(m.SEG_PAIRS,     domain=pyo.Binary)
    m.ord_rp  = pyo.Var(m.SEG_PAIRS_ALL, domain=pyo.Binary)
    m.ord_arena = pyo.Var(m.GG_PAIRS,    domain=pyo.Binary)

    # Group/BIS ordering for judges who also judge groups/BIS
    m.ord_jg  = pyo.Var(m.JG_SEG,  domain=pyo.Binary)
    m.ord_bis = pyo.Var(m.BIS_SEGS, domain=pyo.Binary)

    m.tau_g   = pyo.Var(m.GROUPS, domain=pyo.NonNegativeReals, bounds=(0, T))
    m.tau_bis = pyo.Var(domain=pyo.NonNegativeReals, bounds=(0, T))

    m.ell     = pyo.Var(lunch_judges,
                        domain=pyo.NonNegativeReals,
                        bounds=(t_ls, t_le))
    m.lam     = pyo.Var(m.LUNCH_GAPS, domain=pyo.Binary)
    m.sl_gap  = pyo.Var(m.SL_GAPS,   domain=pyo.Binary)
    m.lunch_pen = pyo.Var(m.SOFT_LUNCH_J, domain=pyo.Binary)
    m.z       = pyo.Var(m.RS_PAIRS, domain=pyo.Binary)

    # Symmetry-breaking auxiliary
    breed_ring_ids = [r for r in ring_ids if r not in grp_rings]
    if len(breed_ring_ids) >= 2:
        m.f_ring = pyo.Var(breed_ring_ids, domain=pyo.NonNegativeReals, bounds=(0, T))

    # -------------------------------------------------------------------
    # Valid lower bounds (MODEL_SPEC §3.4)
    # -------------------------------------------------------------------

    serial_lb: Dict[str, int] = {}
    for jid, jsegs in judge_segs.items():
        serial_lb[jid] = sum(s.duration_slots for s in jsegs)

    # Per-segment start lower bound: must start after all preceding segs of same judge
    cumulative: Dict[str, int] = {jid: 0 for jid in judge_ids}
    for jid, jsegs in judge_segs.items():
        for seg in jsegs:
            lb = cumulative[jid]
            if lb > 0:
                m.start_s[seg.segment_id].setlb(lb)
            cumulative[jid] += seg.duration_slots

    # tau_g lower bounds: group can't start until all its judges' segments are done
    grp_to_seg_judges: Dict[str, set] = {gid: set() for gid in group_ids}
    for seg in segs:
        for bid in seg.breed_ids:
            b = show.breeds.get(bid)
            if b and b.group_id in grp_to_seg_judges:
                grp_to_seg_judges[b.group_id].add(seg.judge_id)

    for gid in group_ids:
        feeding = grp_to_seg_judges[gid]
        if feeding:
            lb = max(serial_lb.get(jid, 0) for jid in feeding)
            if lb > 0:
                m.tau_g[gid].setlb(lb)

    # tau_bis lower bound: after all breed judging
    breed_lb = max(serial_lb.values()) if serial_lb else 0
    if breed_lb > 0:
        m.tau_bis.setlb(breed_lb)

    # -------------------------------------------------------------------
    # Constraints (MODEL_SPEC §3.5)
    # -------------------------------------------------------------------

    # -- C1: Each segment to exactly one ring --
    def c1(m, s):
        return sum(m.u[s, r] for r in ring_ids) == 1
    m.C1 = pyo.Constraint(m.SEGS, rule=c1)

    # -- C2: Segment fits within day --
    def c2(m, s):
        return m.start_s[s] + m.D[s] <= T
    m.C2 = pyo.Constraint(m.SEGS, rule=c2)

    # -- C4: Ring non-overlap (cross-judge pairs, disjunctive) --
    def c4a(m, s1, s2, r):
        M = T - seg_map[s1].duration_slots
        return (m.start_s[s1] + m.D[s1]
                <= m.start_s[s2]
                + M * (1 - m.ord_rp[s1, s2])
                + M * (2 - m.u[s1, r] - m.u[s2, r]))
    def c4b(m, s1, s2, r):
        M = T - seg_map[s2].duration_slots
        return (m.start_s[s2] + m.D[s2]
                <= m.start_s[s1]
                + M * m.ord_rp[s1, s2]
                + M * (2 - m.u[s1, r] - m.u[s2, r]))
    m.C4a = pyo.Constraint(m.SEG_PAIRS_ALL, m.RINGS, rule=c4a)
    m.C4b = pyo.Constraint(m.SEG_PAIRS_ALL, m.RINGS, rule=c4b)

    # -- C5: Judge sequencing (same-judge pairs) --
    def c5a(m, sa, sb):
        M = T - seg_map[sa].duration_slots
        return m.start_s[sa] + m.D[sa] <= m.start_s[sb] + M * (1 - m.ord[sa, sb])
    def c5b(m, sa, sb):
        M = T - seg_map[sb].duration_slots
        return m.start_s[sb] + m.D[sb] <= m.start_s[sa] + M * m.ord[sa, sb]
    m.C5a = pyo.Constraint(m.SEG_PAIRS, rule=c5a)
    m.C5b = pyo.Constraint(m.SEG_PAIRS, rule=c5b)

    # -- C5_trans: Same-judge transitivity (tightens LP relaxation) --
    # For each judge with ≥3 segments, for each ordered triple (a < b < c in list):
    #   ord[a,c] >= ord[a,b] + ord[b,c] - 1   (no a→b→c→a scheduling cycle)
    #   ord[a,c] <= ord[a,b] + ord[b,c]        (no c→b→a→c scheduling cycle)
    # These are the two 3-cycle inequalities of the linear ordering polytope.
    m.C5_trans = pyo.ConstraintList()
    for jid, jsegs in judge_segs.items():
        n = len(jsegs)
        for ai in range(n):
            for bi in range(ai + 1, n):
                for ci in range(bi + 1, n):
                    sa = jsegs[ai].segment_id
                    sb = jsegs[bi].segment_id
                    sc = jsegs[ci].segment_id
                    m.C5_trans.add(m.ord[sa, sc] >= m.ord[sa, sb] + m.ord[sb, sc] - 1)
                    m.C5_trans.add(m.ord[sa, sc] <= m.ord[sa, sb] + m.ord[sb, sc])

    # -- C6: Arena serialization (group events) --
    _grp_dur = {gid: groups[gid].judging_duration_slots for gid in group_ids}
    def c6a_fwd(m, g1, g2):
        M = T - _grp_dur[g1]
        return m.tau_g[g1] + m.Dg[g1] <= m.tau_g[g2] + M * (1 - m.ord_arena[g1, g2])
    def c6a_rev(m, g1, g2):
        M = T - _grp_dur[g2]
        return m.tau_g[g2] + m.Dg[g2] <= m.tau_g[g1] + M * m.ord_arena[g1, g2]
    m.C6a_fwd = pyo.Constraint(m.GG_PAIRS, rule=c6a_fwd)
    m.C6a_rev = pyo.Constraint(m.GG_PAIRS, rule=c6a_rev)

    # -- C7: Breed segments vacate arena before group/BIS --
    def c7a(m, s, g):
        M = T - seg_map[s].duration_slots
        arena_usage = sum(m.u[s, r] for r in grp_rings)
        return m.start_s[s] + m.D[s] <= m.tau_g[g] + M * (1 - arena_usage)
    def c7b(m, s):
        M = T - seg_map[s].duration_slots
        arena_usage = sum(m.u[s, r] for r in grp_rings)
        return m.start_s[s] + m.D[s] <= m.tau_bis + M * (1 - arena_usage)
    m.C7a = pyo.Constraint(m.SEGS, m.GROUPS, rule=c7a)
    m.C7b = pyo.Constraint(m.SEGS, rule=c7b)

    # -- C8: Group waits for all BOBs (no big-M; direct precedence) --
    # Build group → segment lookup
    grp_to_segs: Dict[str, List[str]] = {gid: [] for gid in group_ids}
    for seg in segs:
        for bid in seg.breed_ids:
            b = show.breeds.get(bid)
            if b and b.group_id and seg.segment_id not in grp_to_segs[b.group_id]:
                grp_to_segs[b.group_id].append(seg.segment_id)

    m.C8 = pyo.ConstraintList()
    for gid, gsids in grp_to_segs.items():
        for sid in gsids:
            m.C8.add(m.start_s[sid] + m.D[sid] <= m.tau_g[gid])

    # -- C9: BIS waits for all groups --
    def c9(m, g):
        return m.tau_g[g] + m.Dg[g] <= m.tau_bis
    m.C9 = pyo.Constraint(m.GROUPS, rule=c9)

    # -- C10: All events finish by end of day --
    def c10a(m, g):
        return m.tau_g[g] + m.Dg[g] <= T
    m.C10a = pyo.Constraint(m.GROUPS, rule=c10a)
    m.C10b = pyo.Constraint(expr=m.tau_bis + m.D_BIS <= T)

    # -- C11: Group/BIS judges don't overlap their breed segments --
    def c11a(m, j, g, s):
        M = T - seg_map[s].duration_slots
        return m.start_s[s] + m.D[s] <= m.tau_g[g] + M * (1 - m.ord_jg[j, g, s])
    def c11b(m, j, g, s):
        M = T - _grp_dur[g]
        return m.tau_g[g] + m.Dg[g] <= m.start_s[s] + M * m.ord_jg[j, g, s]
    m.C11a = pyo.Constraint(m.JG_SEG, rule=c11a)
    m.C11b = pyo.Constraint(m.JG_SEG, rule=c11b)

    if bis_segs:
        def c11c(m, s):
            M = T - seg_map[s].duration_slots
            return m.start_s[s] + m.D[s] <= m.tau_bis + M * (1 - m.ord_bis[s])
        def c11d(m, s):
            M = T - D_BIS
            return m.tau_bis + m.D_BIS <= m.start_s[s] + M * m.ord_bis[s]
        m.C11c = pyo.Constraint(m.BIS_SEGS, rule=c11c)
        m.C11d = pyo.Constraint(m.BIS_SEGS, rule=c11d)

    # -- C12: Mandatory lunch break --
    def c12a(m, j):
        n = len(judge_segs[j])
        return sum(m.lam[j, i] for i in range(n - 1)) == 1
    m.C12a = pyo.Constraint(lunch_judges, rule=c12a)

    def c12b(m, j, i):
        s_i = judge_segs[j][i]
        M   = T - t_ls
        return (m.ell[j]
                >= m.start_s[s_i.segment_id] + m.D[s_i.segment_id]
                - M * (1 - m.lam[j, i]))
    m.C12b = pyo.Constraint(m.LUNCH_GAPS, rule=c12b)

    def c12c(m, j, i):
        s_i1 = judge_segs[j][i + 1]
        M    = t_le
        return (m.start_s[s_i1.segment_id]
                >= m.ell[j] + L
                - M * (1 - m.lam[j, i]))
    m.C12c = pyo.Constraint(m.LUNCH_GAPS, rule=c12c)

    def c12d(m, j):
        return m.ell[j] >= t_ls
    def c12e(m, j):
        return m.ell[j] + L <= t_le
    m.C12d = pyo.Constraint(lunch_judges, rule=c12d)
    m.C12e = pyo.Constraint(lunch_judges, rule=c12e)

    # -- C13: Soft lunch availability --
    def c13a(m, j, i):
        s_i = judge_segs[j][i]
        M   = T - (t_le - L)
        return (m.start_s[s_i.segment_id] + m.D[s_i.segment_id]
                <= (t_le - L) + M * (1 - m.sl_gap[j, i]))
    def c13b(m, j, i):
        s_i1 = judge_segs[j][i + 1]
        M    = t_ls
        return (m.start_s[s_i1.segment_id]
                >= t_ls - M * (1 - m.sl_gap[j, i]))
    def c13c(m, j):
        n = len(judge_segs[j]) - 1
        return m.lunch_pen[j] >= 1 - sum(m.sl_gap[j, i] for i in range(n))
    m.C13a = pyo.Constraint(m.SL_GAPS,     rule=c13a)
    m.C13b = pyo.Constraint(m.SL_GAPS,     rule=c13b)
    m.C13c = pyo.Constraint(m.SOFT_LUNCH_J, rule=c13c)

    # -- C15: Ring-switch indicator --
    def c15a(m, j, i):
        s_i  = judge_segs[j][i].segment_id
        s_i1 = judge_segs[j][i + 1].segment_id
        return [pyo.Constraint(expr=m.z[j, i] >= m.u[s_i, r] - m.u[s_i1, r])
                for r in ring_ids]
    # Expand C15 manually (Pyomo can't return lists from rules)
    m.C15 = pyo.ConstraintList()
    for j, i in rs_pairs:
        s_i  = judge_segs[j][i].segment_id
        s_i1 = judge_segs[j][i + 1].segment_id
        for r in ring_ids:
            m.C15.add(m.z[j, i] >= m.u[s_i, r] - m.u[s_i1, r])
            m.C15.add(m.z[j, i] >= m.u[s_i1, r] - m.u[s_i, r])

    # -- C16: Symmetry breaking (ring activation order) --
    if len(breed_ring_ids) >= 2:
        def c16a(m, s, r):
            return m.f_ring[r] <= m.start_s[s] + T * (1 - m.u[s, r])
        m.C16a = pyo.Constraint(m.SEGS, breed_ring_ids, rule=c16a)
        for k in range(len(breed_ring_ids) - 1):
            r1, r2 = breed_ring_ids[k], breed_ring_ids[k + 1]
            setattr(m, f"C16b_{k}",
                    pyo.Constraint(expr=m.f_ring[r1] <= m.f_ring[r2]))

    # -- C_ring_load: Ring-load knapsack cut --
    # Non-overlapping segments assigned to the same ring must collectively fit
    # within the judging window: sum_s D[s]*u[s,r] <= T  for each ring r.
    # This is a valid aggregated bound not implied by the per-segment C2 constraints.
    def c_ring_load(m, r):
        return sum(m.D[s] * m.u[s, r] for s in seg_ids) <= T
    m.C_ring_load = pyo.Constraint(m.RINGS, rule=c_ring_load)

    # -------------------------------------------------------------------
    # Objective (MODEL_SPEC §3.6)
    # -------------------------------------------------------------------
    n_rs   = len(rs_pairs)
    n_sl   = len(soft_lunch_judges)
    L3_max = n_rs + n_sl + 1
    w_L1   = L3_max + 1

    friction = (sum(m.z[j, i] for j, i in rs_pairs)
                + sum(m.lunch_pen[j] for j in soft_lunch_judges))

    m.obj = pyo.Objective(
        expr=w_L1 * m.tau_bis + friction,
        sense=pyo.minimize)

    # -------------------------------------------------------------------
    # Stash metadata for warm-start and solution extraction
    # -------------------------------------------------------------------
    m._show        = show
    m._T           = T
    m._T0          = T0
    m._judge_segs  = judge_segs
    m._lunch_judges = lunch_judges
    m._rs_pairs    = rs_pairs
    m._grp_judge   = grp_judge
    m._bis_jid     = bis_jid
    m._t_ls        = t_ls
    m._t_le        = t_le
    m._L           = L
    m._D_BIS       = D_BIS
    m._grp_dur     = _grp_dur
    m._seg_map     = seg_map
    m._breed_ring_ids = breed_ring_ids

    _log_model_stats(m, show, judge_segs, lunch_judges, soft_lunch_judges,
                     rs_pairs, same_judge_pairs, cross_pairs, jg_seg_triples,
                     len(list(m.C5_trans)))
    return m


def _log_model_stats(m, show, judge_segs, lunch_judges, soft_lunch_judges,
                     rs_pairs, sj_pairs, xj_pairs, jg_triples, n_trans=0):
    import pyomo.environ as pyo
    n_bin  = sum(1 for v in m.component_data_objects(pyo.Var, active=True)
                 if v.domain is pyo.Binary)
    n_cont = sum(1 for v in m.component_data_objects(pyo.Var, active=True)
                 if v.domain is not pyo.Binary)
    log.info(
        "Model built: %d segs | %d rings | T=%d | bin=%d cont=%d",
        len(show.segments), len(show.rings), m._T, n_bin, n_cont)
    log.info(
        "  pairs: same-judge=%d cross=%d  lunch: hard=%d soft=%d  "
        "ring-switch=%d  jg-triples=%d  trans-cuts=%d",
        len(sj_pairs), len(xj_pairs),
        len(lunch_judges), len(soft_lunch_judges),
        len(rs_pairs), len(jg_triples), n_trans)


# ---------------------------------------------------------------------------
# Greedy warm start — solver-independent (MODEL_SPEC §5)
# ---------------------------------------------------------------------------

def _compute_greedy_warmstart(model, show) -> Dict:
    """
    Build a greedy feasible solution and return it as [(VarData, float)].

    This list is solver-independent.  Each backend applies it in its own way:
      - SCIP:  _inject_scip_warmstart() uses the pyscipopt solution API
      - Pyomo: _solve_pyomo() calls vd.set_value() then solver warmstart=True
    """
    T          = model._T
    judge_segs = model._judge_segs
    grp_rings  = show.group_rings
    ring_ids   = list(show.rings.keys())
    segs       = show.segments
    groups     = show.groups
    group_ids  = list(groups.keys())

    t_ls         = model._t_ls
    t_le         = model._t_le
    L            = model._L
    lunch_judges = set(model._lunch_judges)
    seg_map      = model._seg_map

    breed_rings = [r for r in ring_ids if r not in grp_rings]

    # ------------------------------------------------------------------
    # 1. Assign breed segments greedily
    # ------------------------------------------------------------------
    ring_occupied:  Dict[str, list] = {r: [] for r in breed_rings + grp_rings}
    judge_occupied: Dict[str, list] = {jid: [] for jid in show.judges}

    def earliest_slot(seg, ring):
        D        = seg.duration_slots
        occupied = sorted(ring_occupied[ring] + judge_occupied[seg.judge_id])
        t = 0
        for (s, e) in occupied:
            if t + D <= s:
                break
            t = max(t, e)
        return t if t + D <= T else None

    seg_assignment: Dict[str, tuple] = {}   # seg_id → (ring, start_slot)
    lunch_done: set = set()

    for jid in sorted(judge_segs):
        segs_j = judge_segs[jid]
        for seg_i, seg in enumerate(segs_j):
            best_ring, best_t = None, T + 1
            for r in breed_rings:
                t = earliest_slot(seg, r)
                if t is not None and t < best_t:
                    best_t, best_ring = t, r
            if best_ring is None:
                for r in grp_rings:
                    t = earliest_slot(seg, r)
                    if t is not None and t < best_t:
                        best_t, best_ring = t, r
            if best_ring is None:
                raise RuntimeError(f"No feasible slot for {seg.segment_id}")

            # Insert mandatory lunch gap when needed
            if jid in lunch_judges and jid not in lunch_done and seg_i > 0:
                prev_end = max(e for (_, e) in judge_occupied[jid])
                if prev_end >= t_ls:
                    ell_val = max(prev_end, t_ls)
                    best_t  = max(best_t, ell_val + L)
                    lunch_done.add(jid)

            seg_assignment[seg.segment_id] = (best_ring, best_t)
            ring_occupied[best_ring].append((best_t, best_t + seg.duration_slots))
            judge_occupied[jid].append((best_t, best_t + seg.duration_slots))

    # ------------------------------------------------------------------
    # 2. Assign group events
    # ------------------------------------------------------------------
    grp_to_segs_map: Dict[str, set] = {}
    for seg in segs:
        for bid in seg.breed_ids:
            b = show.breeds.get(bid)
            if b and b.group_id:
                grp_to_segs_map.setdefault(b.group_id, set()).add(seg.segment_id)

    def grp_ready(gid):
        sids = grp_to_segs_map.get(gid, set())
        return max(
            (seg_assignment[sid][1] + seg_map[sid].duration_slots
             for sid in sids if sid in seg_assignment),
            default=0)

    arena_free = 0
    grp_assignment: Dict[str, int] = {}
    for gid in sorted(group_ids, key=grp_ready):
        grp    = groups[gid]
        start  = max(grp_ready(gid), arena_free)
        for (s, e) in sorted(judge_occupied.get(grp.judge_id, [])):
            if start + model._grp_dur[gid] > s and start < e:
                start = e
        grp_assignment[gid] = start
        arena_free = start + model._grp_dur[gid]
        judge_occupied.setdefault(grp.judge_id, []).append(
            (start, start + model._grp_dur[gid]))

    # ------------------------------------------------------------------
    # 3. BIS
    # ------------------------------------------------------------------
    bis_start = arena_free
    if model._bis_jid:
        for (s, e) in sorted(judge_occupied.get(model._bis_jid, [])):
            if bis_start + model._D_BIS > s and bis_start < e:
                bis_start = e

    # ------------------------------------------------------------------
    # 4. Build [(VarData, value)] list  (VarData is not hashable in all Pyomo versions)
    # ------------------------------------------------------------------
    ws: list = []

    def put(vd, val):
        ws.append((vd, float(val)))

    # u[s,r] and start_s[s]
    for sid, (ring, t) in seg_assignment.items():
        for r in ring_ids:
            put(model.u[sid, r], 1.0 if r == ring else 0.0)
        put(model.start_s[sid], t)

    # ord[sa,sb] — same-judge ordering
    for (sa, sb) in model.SEG_PAIRS:
        if sa in seg_assignment and sb in seg_assignment:
            put(model.ord[sa, sb],
                1.0 if seg_assignment[sa][1] <= seg_assignment[sb][1] else 0.0)

    # ord_rp[s1,s2] — cross-judge ordering
    for (s1, s2) in model.SEG_PAIRS_ALL:
        if s1 in seg_assignment and s2 in seg_assignment:
            put(model.ord_rp[s1, s2],
                1.0 if seg_assignment[s1][1] <= seg_assignment[s2][1] else 0.0)

    # tau_g and ord_arena
    for gid, t_g in grp_assignment.items():
        put(model.tau_g[gid], t_g)
    for (g1, g2) in model.GG_PAIRS:
        if g1 in grp_assignment and g2 in grp_assignment:
            put(model.ord_arena[g1, g2],
                1.0 if grp_assignment[g1] <= grp_assignment[g2] else 0.0)

    # tau_bis
    put(model.tau_bis, bis_start)

    # ord_jg
    for (j, g, s) in model.JG_SEG:
        if s in seg_assignment and g in grp_assignment:
            t_end_s = seg_assignment[s][1] + seg_map[s].duration_slots
            put(model.ord_jg[j, g, s],
                1.0 if t_end_s <= grp_assignment[g] else 0.0)

    # ord_bis
    for s in model.BIS_SEGS:
        if s in seg_assignment:
            t_end_s = seg_assignment[s][1] + seg_map[s].duration_slots
            put(model.ord_bis[s], 1.0 if t_end_s <= bis_start else 0.0)

    # z[j,i] — ring switches
    for j, i in model._rs_pairs:
        jsegs_j = judge_segs[j]
        if i < len(jsegs_j) - 1:
            sa = jsegs_j[i].segment_id
            sb = jsegs_j[i + 1].segment_id
            if sa in seg_assignment and sb in seg_assignment:
                same = seg_assignment[sa][0] == seg_assignment[sb][0]
                put(model.z[j, i], 0.0 if same else 1.0)

    # ell[j] and lam[j,i] — use model._judge_segs for consistent indexing
    m_jsegs = model._judge_segs
    for jid in model._lunch_judges:
        msegs = m_jsegs.get(jid, [])
        n     = len(msegs)
        if n < 2:
            continue
        gap_i = 0
        for i in range(n - 1):
            sid = msegs[i].segment_id
            if sid in seg_assignment:
                if seg_assignment[sid][1] + msegs[i].duration_slots >= t_ls:
                    gap_i = i
                    break
        sid_g   = msegs[gap_i].segment_id
        end_g   = (seg_assignment[sid_g][1] + msegs[gap_i].duration_slots
                   if sid_g in seg_assignment else t_ls)
        ell_val = max(t_ls, min(end_g, t_le - L))
        put(model.ell[jid], ell_val)
        for i in range(n - 1):
            if (jid, i) in model.lam:
                put(model.lam[jid, i], 1.0 if i == gap_i else 0.0)

    # sl_gap[j,i] and lunch_pen[j]
    for jid in model.SOFT_LUNCH_J:
        msegs  = m_jsegs.get(jid, [])
        any_ok = False
        for i in range(len(msegs) - 1):
            if (jid, i) not in model.sl_gap:
                continue
            sid_i  = msegs[i].segment_id
            sid_i1 = msegs[i + 1].segment_id
            if sid_i not in seg_assignment or sid_i1 not in seg_assignment:
                continue
            end_i      = seg_assignment[sid_i][1] + msegs[i].duration_slots
            start_next = seg_assignment[sid_i1][1]
            ok = end_i <= t_le - L and start_next >= t_ls
            put(model.sl_gap[jid, i], 1.0 if ok else 0.0)
            if ok:
                any_ok = True
        put(model.lunch_pen[jid], 0.0 if any_ok else 1.0)

    # f_ring[r] — symmetry-breaking
    if hasattr(model, 'f_ring'):
        for r in model._breed_ring_ids:
            segs_in_r = [sid for sid, (ra, _) in seg_assignment.items() if ra == r]
            earliest  = min(seg_assignment[sid][1] for sid in segs_in_r) if segs_in_r else 0
            put(model.f_ring[r], earliest)

    log.info("Warm-start: BIS slot %d  (%s)",
             model._T0 + bis_start,
             show.params.slot_to_hhmm(model._T0 + bis_start))
    return ws


# ---------------------------------------------------------------------------
# LP variable name mapping
# ---------------------------------------------------------------------------

def _build_lp_var_map(model, lp_path: str) -> dict:
    """
    Parse the LP file to get ground-truth SCIP variable names, then match
    them to Pyomo VarData objects.  Returns {lp_name: VarData}.
    """
    import pyomo.environ as pyo

    # Collect LP variable names
    lp_names: set = set()
    in_section = False
    with open(lp_path) as f:
        for line in f:
            ls = line.strip()
            if ls.lower() in ('bounds', 'generals', 'binary', 'binaries',
                               'general', 'integers'):
                in_section = True
                continue
            if ls.lower() in ('end',):
                in_section = False
                continue
            if in_section and ls and not ls.startswith('\\'):
                for token in ls.split():
                    if token.startswith('+') or token.startswith('-'):
                        continue
                    try:
                        float(token)
                    except ValueError:
                        lp_names.add(token)

    # Build candidate transformations of Pyomo name → LP name
    def candidates(pyomo_name: str):
        """Generate candidate LP names from a Pyomo variable name."""
        # Pyomo name: var_name[idx1,idx2,...] or var_name[idx]
        # LP file: typically parentheses, underscores, stripped quotes
        import re
        # Strip surrounding quotes from each component
        def strip_q(s):
            return s.strip("'\"")

        # Split at first '[', extract base and index parts
        m_bracket = re.match(r'^([^[]+)\[(.+)\]$', pyomo_name)
        if not m_bracket:
            yield pyomo_name
            return
        base, idx_str = m_bracket.group(1), m_bracket.group(2)
        # Split indices by comma (but not inside brackets)
        # Simple split (indices don't nest here)
        parts = [strip_q(p.strip()) for p in idx_str.split(',')]

        # Various mangling patterns observed in practice:
        joined_underscore = '_'.join(parts)
        joined_double     = '__'.join(parts)
        joined_trail      = '__'.join(p + '_' for p in parts)

        yield f"{base}({joined_underscore})"
        yield f"{base}({joined_double})"
        yield f"{base}({'__'.join(parts)}_)"
        yield f"{base}({joined_trail}"[:-1] + ")"  # trailing _ on last
        # With underscores replacing spaces in base
        base2 = base.replace(' ', '_')
        yield f"{base2}({joined_underscore})"
        yield f"{base2}({joined_double})"
        # With original (un-stripped) index — handles f_ring['1'] → f_ring('1')
        raw_parts = [p.strip() for p in idx_str.split(',')]
        yield f"{base}({','.join(raw_parts)})"
        yield f"{base2}({','.join(raw_parts)})"

    result = {}
    unmatched = []
    for vd in model.component_data_objects(pyo.Var, active=True):
        pyomo_name = vd.name
        matched = False
        for cand in candidates(pyomo_name):
            if cand in lp_names:
                result[cand] = vd
                matched = True
                break
        if not matched:
            unmatched.append(pyomo_name)

    n_total = len(result) + len(unmatched)
    if unmatched:
        log.warning("LP var map: %d/%d unmatched. Sample: %s",
                    len(unmatched), n_total, unmatched[:5])
    else:
        log.info("LP var map: all %d vars matched", n_total)
    return result


# ---------------------------------------------------------------------------
# Solution extraction
# ---------------------------------------------------------------------------

def _extract_result(model, scip, lp_var_map, show, params, elapsed) -> SolveResult:
    """Load SCIP solution into Pyomo, then build SolveResult."""
    import pyscipopt

    status_map = {
        pyscipopt.SCIP_STATUS.OPTIMAL:    "OPTIMAL",
        pyscipopt.SCIP_STATUS.TIMELIMIT:  "FEASIBLE",
        pyscipopt.SCIP_STATUS.INFEASIBLE: "INFEASIBLE",
        pyscipopt.SCIP_STATUS.UNBOUNDED:  "ERROR",
    }
    scip_status = scip.getStatus()
    status = status_map.get(scip_status, "FEASIBLE")
    gap    = scip.getGap() if scip.getNSols() > 0 else 1.0

    if scip.getNSols() == 0:
        log.error("No solution found (status=%s)", scip_status)
        return SolveResult(status="INFEASIBLE", mip_gap=1.0,
                           solve_time_sec=elapsed, bis_start_slot=0)

    # Load solution values back into Pyomo
    scip_var_map    = {v.name: v for v in scip.getVars()}
    pyomo_to_scip   = {id(vd): scip_var_map[lp_name]
                       for lp_name, vd in lp_var_map.items()
                       if lp_name in scip_var_map}

    import pyomo.environ as pyo

    def safe_val(pyomo_var):
        sv = pyomo_to_scip.get(id(pyomo_var))
        if sv is None:
            return 0.0
        try:
            return scip.getSolVal(scip.getBestSol(), sv)
        except Exception:
            return 0.0

    # Segment schedules
    T0         = model._T0
    judge_segs = model._judge_segs
    seg_map    = model._seg_map
    ring_ids   = list(show.rings.keys())

    seg_schedules = []
    for s in show.segments:
        sid    = s.segment_id
        t_start = int(round(safe_val(model.start_s[sid])))
        ring   = next((r for r in ring_ids if safe_val(model.u[sid, r]) > 0.5), ring_ids[0])
        seg_schedules.append(SegmentSchedule(
            segment_id=sid,
            judge_id=s.judge_id,
            ring_id=ring,
            start_slot=T0 + t_start,
            end_slot=T0 + t_start + s.duration_slots,
            n_dogs=s.n_dogs,
            breed_ids=s.breed_ids,
        ))

    # Group schedules
    grp_schedules = []
    arena_label = "+".join(sorted(show.group_rings))
    for gid in show.groups:
        grp    = show.groups[gid]
        t_g    = int(round(safe_val(model.tau_g[gid])))
        dur    = model._grp_dur[gid]
        grp_schedules.append(GroupSchedule(
            group_id=gid,
            judge_id=grp.judge_id,
            ring_id=arena_label,
            start_slot=T0 + t_g,
            end_slot=T0 + t_g + dur,
            n_breeds=len(grp.breed_ids) if hasattr(grp, 'breed_ids') else 0,
        ))

    # BIS start
    bis_start = T0 + int(round(safe_val(model.tau_bis)))

    # Lunch slots
    lunch_slots = {}
    for jid in model._lunch_judges:
        lunch_slots[jid] = T0 + int(round(safe_val(model.ell[jid])))

    # Friction counts
    rs  = sum(1 for j, i in model._rs_pairs if safe_val(model.z[j, i]) > 0.5)
    eq  = sum(1 for s in show.segments if s.has_equipment_mix)

    result = SolveResult(
        status=status,
        mip_gap=gap,
        solve_time_sec=elapsed,
        bis_start_slot=bis_start,
        segments=seg_schedules,
        groups=sorted(grp_schedules, key=lambda g: g.start_slot),
        lunch_slots=lunch_slots,
        equip_switches=eq,
        ring_switches=rs,
        n_conflicts=0,
    )

    p = show.params
    log.info("Solve result: %s  gap=%.2f%%  time=%.1fs  BIS=%s",
             status, gap * 100, elapsed, p.slot_to_hhmm(bis_start))
    log.info("  Ring switches: %d  Equip switches: %d", rs, eq)

    return result


# ---------------------------------------------------------------------------
# Model statistics (standalone utility)
# ---------------------------------------------------------------------------

def model_stats(show, params: Optional[SolveParams] = None) -> dict:
    if params is None:
        params = SolveParams()
    m = _build_model(show, params)
    import pyomo.environ as pyo
    n_bin  = sum(1 for v in m.component_data_objects(pyo.Var, active=True)
                 if v.domain is pyo.Binary)
    n_cont = sum(1 for v in m.component_data_objects(pyo.Var, active=True)
                 if v.domain is not pyo.Binary)
    n_con  = sum(1 for c in m.component_data_objects(pyo.Constraint, active=True))
    return {"binary_vars": n_bin, "cont_vars": n_cont, "constraints": n_con}
