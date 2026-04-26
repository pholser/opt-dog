"""
akc_cpsat.py — AKC All-Breed Show Scheduling: OR-Tools CP-SAT solver
=====================================================================

Implements solve_show(show, params) -> SolveResult using OR-Tools CP-SAT.

CP-SAT advantages over MIP for this problem
--------------------------------------------
  * AddNoOverlap replaces all big-M disjunctive constraints (C4, C5, C6,
    C11, C12) with native interval-scheduling propagation — no LP relaxation
    weakness from fractional ring assignments.
  * Domain propagation finds good feasible solutions rapidly.
  * Integer variables with tight bounds make the search space much smaller.

Architecture
------------
  solve_show(show, params)    Public entry point
  _solve_cpsat(show, params)  Build + solve CP-SAT model, return SolveResult

Constraint mapping from MODEL_SPEC
-----------------------------------
  C1  ring assignment         → AddExactlyOne(pres[s][r] for r)
  C2  window                  → upper bound on start[s]: start[s] <= T - D[s]
  C4  ring non-overlap        → AddNoOverlap(ring_iv[s][r] for s) per ring r
  C5  same-judge sequencing   → AddNoOverlap(judge_timeline[j])
  C6  arena serialization     → AddNoOverlap(arena_timeline)
  C7  vacate arena            → eliminated; arena ring chosen post-hoc via
                                 assign_arena_ring() (from akc_schedule)
  C8  group waits for BOBs    → direct Add(end_s[s] <= tau_g[g])
  C9  BIS waits for groups    → direct Add(end_g[g] <= tau_bis)
  C10 end of day              → upper bounds on tau_g, tau_bis
  C11 judge doesn't overlap   → judge_timeline includes group_iv and bis_iv
       own group/BIS           (same AddNoOverlap as C5)
  C12 mandatory lunch         → required lunch IntervalVar in judge_timeline
  C13 soft lunch penalty      → OnlyEnforceIf constraints + AddBoolOr
  C15 ring switch cost        → same_r BoolVars + sum(same_r) + sw == 1
  C16 symmetry breaking       → omitted (CP-SAT handles symmetry natively)
"""

from __future__ import annotations

import logging
import math
import sys
import time
from typing import Dict, List, Optional

from akc_schedule import (
    SolveParams, SolveResult, SegmentSchedule, GroupSchedule, assign_arena_ring,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def solve_show(show, params: Optional[SolveParams] = None) -> SolveResult:
    """Solve an AKC show with OR-Tools CP-SAT."""
    if params is None:
        params = SolveParams(solver="cpsat")
    return _solve_cpsat(show, params)


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

def _solve_cpsat(show, params: SolveParams) -> SolveResult:
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        raise ImportError("OR-Tools is required: pip install ortools")

    t0  = time.time()
    p   = show.params
    T0  = p.judging_start_slot
    T_min = p.slot_minutes

    segs    = show.segments
    seg_map = {s.segment_id: s for s in segs}
    seg_ids = [s.segment_id for s in segs]
    dur     = {s.segment_id: s.duration_slots for s in segs}

    ring_ids    = list(show.rings.keys())
    breed_rings = ring_ids   # arena ring chosen post-hoc; all rings for breeds

    groups    = show.groups
    group_ids = list(groups.keys())
    judge_ids = list(show.judges.keys())

    # Segments per judge (same order as preprocessing)
    judge_segs: Dict[str, List] = {jid: [] for jid in judge_ids}
    for s in segs:
        judge_segs[s.judge_id].append(s)

    # --- Timing parameters (relative to T0) --------------------------------
    D_BIS = math.ceil(20 / T_min)
    L     = p.lunch_duration_slots
    t_ls  = p.lunch_start_slot - T0
    t_le  = p.lunch_end_slot   - T0

    lunch_judges_set  = set(show.judges_requiring_lunch)
    soft_lunch_judges = [
        jid for jid in judge_ids
        if jid not in lunch_judges_set and len(judge_segs.get(jid, [])) >= 2
    ]

    # --- Horizon (critical-path upper bound) --------------------------------
    max_judge_load = max(
        (sum(s.duration_slots for s in judge_segs[jid])
         + (L if jid in lunch_judges_set else 0))
        for jid in judge_ids
    ) if judge_ids else 0
    T = (max_judge_load
         + sum(g.judging_duration_slots for g in groups.values())
         + D_BIS)
    log.info("CP-SAT horizon T=%d slots (%d min)", T, T * T_min)

    # --- BIS lower bound (critical-path + load-balance) --------------------
    def _judge_lb(jid):
        return (sum(s.duration_slots for s in judge_segs[jid])
                + (L if jid in lunch_judges_set else 0))

    critical_lb = max(
        (_judge_lb(jid) for jid in judge_ids if judge_segs.get(jid)),
        default=0,
    )
    if breed_rings:
        W  = sum(s.duration_slots for s in segs)
        lb_par = math.ceil(W / len(breed_rings))
        if lb_par > critical_lb:
            log.info("bis_lb: load-balance %d > critical-path %d → using %d",
                     lb_par, critical_lb, lb_par)
            critical_lb = lb_par

    # Lower bound on tau_bis = critical_lb (max single-judge load).
    # Groups may overlap breed judging; C8/C9 enforce group/BIS ordering.
    # Clamp to the variable's upper bound in case the load-balance term
    # exceeds T − D_BIS (which would produce an empty domain).
    bis_lb = min(critical_lb, T - D_BIS)
    log.info("CP-SAT tau_bis ∈ [%d, %d]", bis_lb, T - D_BIS)

    # --- Precompute which segments feed each group --------------------------
    grp_feeding_segs: Dict[str, set] = {gid: set() for gid in group_ids}
    for seg in segs:
        for bid in seg.breed_ids:
            b = show.breeds.get(bid)
            if b and b.group_id in grp_feeding_segs:
                grp_feeding_segs[b.group_id].add(seg.segment_id)

    bis_jid = show.bis_judge_id

    # -----------------------------------------------------------------------
    # Build CP-SAT model
    # -----------------------------------------------------------------------

    model = cp_model.CpModel()

    # --- Segment start / end IntVars ----------------------------------------
    start = {}   # sid → IntVar (relative to T0)
    end_s = {}   # sid → IntVar
    for s in segs:
        sid = s.segment_id
        ub  = T - dur[sid]
        start[sid] = model.NewIntVar(0, ub,       f"start_{sid}")
        end_s[sid] = model.NewIntVar(dur[sid], T, f"end_{sid}")
        model.Add(end_s[sid] == start[sid] + dur[sid])

    # --- Ring assignment BoolVars and optional interval vars ----------------
    pres    = {sid: {} for sid in seg_ids}   # pres[sid][rid] → BoolVar
    ring_iv = {sid: {} for sid in seg_ids}   # ring_iv[sid][rid] → OptionalIntervalVar

    for sid in seg_ids:
        d = dur[sid]
        for rid in ring_ids:
            b = model.NewBoolVar(f"pres_{sid}_{rid}")
            pres[sid][rid] = b
            ring_iv[sid][rid] = model.NewOptionalIntervalVar(
                start[sid], d, end_s[sid], b, f"ringiv_{sid}_{rid}")

    # --- Group start / end IntVars and required interval vars ---------------
    tau_g  = {}   # gid → IntVar
    end_g  = {}   # gid → IntVar
    grp_iv = {}   # gid → IntervalVar  (arena NoOverlap + judge timeline)

    for gid, grp in groups.items():
        dg = grp.judging_duration_slots
        tau_g[gid] = model.NewIntVar(0, T - dg, f"tau_g_{gid}")
        end_g[gid] = model.NewIntVar(dg, T,      f"end_g_{gid}")
        model.Add(end_g[gid] == tau_g[gid] + dg)
        grp_iv[gid] = model.NewIntervalVar(
            tau_g[gid], dg, end_g[gid], f"grp_iv_{gid}")

    # --- BIS start / end / interval var ------------------------------------
    tau_bis = model.NewIntVar(max(bis_lb, 0), T - D_BIS, "tau_bis")
    end_bis = model.NewIntVar(D_BIS, T, "end_bis")
    model.Add(end_bis == tau_bis + D_BIS)
    bis_iv  = model.NewIntervalVar(tau_bis, D_BIS, end_bis, "bis_iv")

    # --- Required interval vars for judge timeline -------------------------
    # (segments always participate in the judge's NoOverlap; the ring_iv are
    #  optional so we create separate required intervals for the judge axis)
    judge_seg_iv = {}
    for s in segs:
        sid = s.segment_id
        judge_seg_iv[sid] = model.NewIntervalVar(
            start[sid], dur[sid], end_s[sid], f"judgeiv_{sid}")

    # --- Mandatory lunch vars (C12) ----------------------------------------
    lunch_start_var: Dict[str, object] = {}
    lunch_end_var:   Dict[str, object] = {}
    lunch_iv:        Dict[str, object] = {}

    for jid in lunch_judges_set:
        ls_v = model.NewIntVar(t_ls, t_le - L, f"lunchstart_{jid}")
        le_v = model.NewIntVar(t_ls + L, t_le,  f"lunchend_{jid}")
        model.Add(le_v == ls_v + L)
        lunch_start_var[jid] = ls_v
        lunch_end_var[jid]   = le_v
        lunch_iv[jid] = model.NewIntervalVar(ls_v, L, le_v, f"lunchiv_{jid}")

    # --- Soft lunch vars (C13) --------------------------------------------
    sl_active:  Dict[str, Dict[int, object]] = {}
    sl_start_v: Dict[str, Dict[int, object]] = {}
    lunch_pen:  Dict[str, object] = {}

    for jid in soft_lunch_judges:
        jsegs  = judge_segs[jid]
        n_gaps = len(jsegs) - 1
        sl_active[jid]  = {}
        sl_start_v[jid] = {}
        for i in range(n_gaps):
            sa = jsegs[i]
            sb = jsegs[i + 1]
            active = model.NewBoolVar(f"slactive_{jid}_{i}")
            sl_s   = model.NewIntVar(t_ls, t_le - L, f"slstart_{jid}_{i}")
            sl_e   = model.NewIntVar(t_ls + L, t_le,  f"slend_{jid}_{i}")
            # When active: lunch starts after sa ends, sb starts after lunch ends,
            # and the lunch fits inside the window (enforced by variable bounds +
            # the ordering constraints below).
            model.Add(sl_e == sl_s + L)
            sl_active[jid][i]  = active
            sl_start_v[jid][i] = sl_s

        pen = model.NewBoolVar(f"lunchpen_{jid}")
        lunch_pen[jid] = pen

    # --- Ring-switch BoolVars (C15) ----------------------------------------
    switch_vars: List = []   # list of sw BoolVars for objective
    rs_pairs_list = []       # (jid, i, sa_sid, sb_sid)
    sw_vars:     Dict = {}   # (jid, i) → sw BoolVar
    same_r_vars: Dict = {}   # (jid, i) → {rid: sr BoolVar}

    for jid in judge_ids:
        jsegs = judge_segs[jid]
        for i in range(len(jsegs) - 1):
            sa = jsegs[i]
            sb = jsegs[i + 1]
            sw = model.NewBoolVar(f"sw_{jid}_{i}")
            switch_vars.append(sw)
            rs_pairs_list.append((jid, i, sa.segment_id, sb.segment_id))
            sw_vars[jid, i] = sw
            same_r_vars[jid, i] = {
                rid: model.NewBoolVar(f"samer_{jid}_{i}_{rid}")
                for rid in ring_ids
            }

    # =======================================================================
    # Objective  (§3.6 of MODEL_SPEC)
    # w_L1 · tau_bis  +  w_L3 · (Σ sw  +  Σ lunch_pen)
    # w_L1 large enough that any 1-slot BIS improvement dominates all L3 gains
    # =======================================================================

    L3_max = len(rs_pairs_list) + len(soft_lunch_judges) + 1
    w_L3 = 1
    w_L1 = L3_max + 1

    model.Minimize(
        w_L1 * tau_bis
        + w_L3 * sum(switch_vars)
        + w_L3 * sum(lunch_pen.values())
    )

    # =======================================================================
    # C1 — Each segment assigned to exactly one ring  (§3.5 C1)
    # =======================================================================

    for sid in seg_ids:
        model.AddExactlyOne(pres[sid][rid] for rid in ring_ids)

    # =======================================================================
    # C5 — Same-judge sequencing  (§3.5 C5)
    # A judge's segments must not overlap in time.  AddNoOverlap on the
    # required judge_seg_iv intervals enforces this without big-M.
    # =======================================================================

    for jid, jsegs in judge_segs.items():
        if len(jsegs) >= 2:
            model.AddNoOverlap(judge_seg_iv[s.segment_id] for s in jsegs)

    # =======================================================================
    # C4 — Ring non-overlap  (§3.5 C4)
    # For each ring, the optional interval vars (active iff pres[sid][rid]=1)
    # must not overlap — enforces that at most one segment occupies a ring
    # at any time, across all judges.
    # =======================================================================

    for rid in ring_ids:
        model.AddNoOverlap(ring_iv[sid][rid] for sid in seg_ids)

    # =======================================================================
    # C11 — Group/BIS judges don't overlap their own breed segments  (§3.5 C11)
    # Extend each such judge's NoOverlap timeline to include their group and
    # BIS interval vars alongside their breed segment intervals.
    # =======================================================================

    for jid, jsegs in judge_segs.items():
        extra = [grp_iv[gid] for gid, grp in groups.items() if grp.judge_id == jid]
        if jid == bis_jid:
            extra.append(bis_iv)
        if extra:
            model.AddNoOverlap(
                [judge_seg_iv[s.segment_id] for s in jsegs] + extra)

    # =======================================================================
    # C12 — Mandatory lunch break  (§3.5 C12)
    # Add each mandatory-break judge's lunch interval to their NoOverlap
    # timeline.  Variable bounds already enforce the window [t_ls, t_le−L].
    # =======================================================================

    for jid in lunch_judges_set:
        jsegs = judge_segs[jid]
        model.AddNoOverlap(
            [judge_seg_iv[s.segment_id] for s in jsegs] + [lunch_iv[jid]])

    # =======================================================================
    # C6 — Arena serialization: group events don't overlap  (§3.5 C6)
    # Pure temporal — no ring variable (ring chosen post-hoc).
    # =======================================================================

    if len(group_ids) >= 2:
        model.AddNoOverlap(grp_iv[gid] for gid in group_ids)

    # =======================================================================
    # C8 — Group waits for all its BOB segments  (§3.5 C8)
    # C9 — BIS waits for all groups              (§3.5 C9)
    # C10 — All events fit in the day            (§3.5 C10)
    #        already encoded: tau_g ∈ [0, T−dg], tau_bis ∈ [bis_lb, T−D_BIS]
    # =======================================================================

    for gid, sids in grp_feeding_segs.items():
        for sid in sids:
            model.Add(end_s[sid] <= tau_g[gid])          # C8

    for gid in group_ids:
        model.Add(end_g[gid] <= tau_bis)                  # C9

    # =======================================================================
    # C15 — Ring-switch indicator  (§3.5 C15)
    # same_r[rid] = AND(pres[sa][rid], pres[sb][rid])
    # sw = 1 − Σ same_r  (switch iff no ring is shared)
    # =======================================================================

    for jid, i, sa_sid, sb_sid in rs_pairs_list:
        sr_map = same_r_vars[jid, i]
        sw     = sw_vars[jid, i]
        for rid, sr in sr_map.items():
            model.AddMinEquality(sr, [pres[sa_sid][rid], pres[sb_sid][rid]])
        model.Add(sum(sr_map.values()) + sw == 1)

    # =======================================================================
    # C13 — Soft lunch availability  (§3.5 C13)
    # sl_active[j][i]=1 iff gap i qualifies; penalty fires if none qualify.
    # Variable bounds already enforce sl_start ∈ [t_ls, t_le−L].
    # =======================================================================

    for jid in soft_lunch_judges:
        jsegs  = judge_segs[jid]
        pen    = lunch_pen[jid]
        n_gaps = len(jsegs) - 1
        for i in range(n_gaps):
            sa_sid = jsegs[i].segment_id
            sb_sid = jsegs[i + 1].segment_id
            active = sl_active[jid][i]
            sl_s   = sl_start_v[jid][i]
            # When active: lunch starts after seg_i ends
            model.Add(sl_s >= end_s[sa_sid]).OnlyEnforceIf(active)
            # When active: seg_{i+1} starts after lunch ends
            model.Add(sl_s + L <= start[sb_sid]).OnlyEnforceIf(active)
            # Active gap implies no penalty
            model.AddImplication(active, pen.Not())
        # At least one gap active or penalty fires
        model.AddBoolOr([sl_active[jid][i] for i in range(n_gaps)] + [pen])

    # =======================================================================
    # Solve
    # =======================================================================

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(params.time_limit_sec)
    solver.parameters.relative_gap_limit  = params.gap
    if params.threads:
        solver.parameters.num_workers = params.threads
    if params.tee:
        solver.parameters.log_search_progress = True

    # Callback to print progress (similar to the MIP incumbent logger)
    class _IncumbentCb(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__()
            self._n = 0

        def on_solution_callback(self):
            self._n += 1
            elapsed   = time.time() - t0
            obj       = self.ObjectiveValue()
            bound     = self.BestObjectiveBound()
            tau_val   = int(obj // w_L1)
            l3_val    = int(round(obj - w_L1 * tau_val))
            gap_pct   = 0.0
            if abs(obj) > 1e-6:
                gap_pct = max(0.0, (obj - bound) / obj) * 100
            bis_str   = p.slot_to_hhmm(T0 + tau_val)
            conflicts = self.NumConflicts()
            branches  = self.NumBranches()
            print(
                f"  [{elapsed:6.1f}s]  #{self._n:3d}  BIS {bis_str}"
                f"  friction {l3_val}"
                f"  gap {gap_pct:.2f}%"
                f"  conflicts {conflicts:,}  branches {branches:,}"
                f"  (obj {obj:.1f}  lb {bound:.1f})",
                file=sys.stderr, flush=True,
            )

    cb = _IncumbentCb()
    status_code = solver.Solve(model, cb)

    elapsed = time.time() - t0
    status_name = solver.StatusName(status_code)
    log.info("CP-SAT status=%s  time=%.1fs  conflicts=%d",
             status_name, elapsed, solver.NumConflicts())

    # Map CP-SAT status to SolveResult status string
    if status_name == "OPTIMAL":
        status = "OPTIMAL"
    elif status_name == "FEASIBLE":
        status = "FEASIBLE"
    elif status_name == "INFEASIBLE":
        return SolveResult(status="INFEASIBLE", gap=1.0,
                           solve_time_sec=elapsed, bis_start_slot=0,
                           n_conflicts=solver.NumConflicts(), show=show)
    else:
        return SolveResult(status="ERROR", gap=1.0,
                           solve_time_sec=elapsed, bis_start_slot=0,
                           n_conflicts=solver.NumConflicts(), show=show)

    # Compute MIP gap
    obj   = solver.ObjectiveValue()
    bound = solver.BestObjectiveBound()
    gap   = max(0.0, (obj - bound) / obj) if abs(obj) > 1e-6 else 0.0

    # --- Extract solution ---------------------------------------------------
    def val(v):
        return solver.Value(v)

    # Segment schedules
    seg_schedules = []
    for s in segs:
        sid      = s.segment_id
        t_start  = val(start[sid])
        ring     = next((r for r in ring_ids if val(pres[sid][r]) > 0), ring_ids[0])
        seg_schedules.append(SegmentSchedule(
            segment_id=sid,
            judge_id=s.judge_id,
            ring_id=ring,
            start_slot=T0 + t_start,
            end_slot=T0 + t_start + s.duration_slots,
            n_dogs=s.n_dogs,
            breed_ids=s.breed_ids,
        ))

    # Group schedules — ring assigned by post-processing
    group_opt = {gid: T0 + val(tau_g[gid]) for gid in group_ids}
    bis_opt   = T0 + val(tau_bis)
    grp_schedules, bis_start = assign_arena_ring(
        seg_schedules, group_opt, bis_opt, show)

    # Lunch slots (mandatory: exact start; soft: first qualifying gap or None)
    lunch_slots: Dict[str, int] = {}
    for jid in lunch_judges_set:
        lunch_slots[jid] = T0 + val(lunch_start_var[jid])
    for jid in soft_lunch_judges:
        for i, active in sl_active[jid].items():
            if val(active) > 0:
                lunch_slots[jid] = T0 + val(sl_start_v[jid][i])
                break

    # Ring switches
    rs = sum(val(sw) for sw in switch_vars)

    eq = sum(1 for s in segs if s.has_equipment_mix)

    log.info("Solve result: %s  gap=%.2f%%  time=%.1fs  BIS=%s  ring_sw=%d",
             status, gap * 100, elapsed, p.slot_to_hhmm(bis_start), rs)

    return SolveResult(
        status=status,
        gap=gap,
        solve_time_sec=elapsed,
        bis_start_slot=bis_start,
        segments=seg_schedules,
        groups=sorted(grp_schedules, key=lambda g: g.start_slot),
        lunch_slots=lunch_slots,
        equip_switches=eq,
        ring_switches=rs,
        n_conflicts=solver.NumConflicts(),
        show=show,
    )
