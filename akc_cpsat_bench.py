"""
akc_cpsat_bench.py — Incremental CP-SAT constraint benchmark
=============================================================

Builds the CP-SAT model one constraint family at a time (each step is
cumulative), solves for a fixed time budget, and prints a comparison table
measuring:

  Goal 2 — Solving speed per constraint family:
    first_incumb_sec  time to first feasible solution
    n_incumbents      how many improving solutions found in budget
    wall_time         actual solve time

  Goal 3 — Domain reduction per constraint family:
    gap_pct           optimality gap at end (lower = tighter bound)
    conflicts         total solver conflicts (backtracking steps)
    branches          total decisions made
    conf/branch       ratio: high = strong propagation per decision

Steps (cumulative — each includes all prior):
    00  vars + bounds only
    01  +C1   ring assignment (AddExactlyOne)
    02  +C8/C9/C10  BOB → group → BIS precedences
    03  +C4   ring NoOverlap per ring
    04  +C5+C11  judge timeline NoOverlap (breeds + groups + BIS)
    05  +C12  mandatory lunch interval in judge timeline
    06  +C6+C7  arena NoOverlap (groups + BIS + breed segs in arena rings)
    07  +C13  soft-lunch penalty (OnlyEnforceIf + AddBoolOr)
    08  +C15  ring-switch cost  ← full model

Usage
-----
    python akc_cpsat_bench.py show.xlsx [--time N] [--slot-minutes N]

--time N    seconds per step (default 30)
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional

# ── shared infrastructure ──────────────────────────────────────────────────────

STEPS: List[tuple] = [
    ("00_vars_bounds",    frozenset()),
    ("01_C1",             frozenset({"C1"})),
    ("02_C8_C9_C10",      frozenset({"C1", "C8", "C9", "C10"})),
    ("03_C4_ring",        frozenset({"C1", "C8", "C9", "C10", "C4"})),
    ("04_C5_C11_judge",   frozenset({"C1", "C8", "C9", "C10", "C4", "C5"})),
    ("05_C12_lunch",      frozenset({"C1", "C8", "C9", "C10", "C4", "C5", "C12"})),
    ("06_C6_C7_arena",    frozenset({"C1", "C8", "C9", "C10", "C4", "C5", "C12", "C6"})),
    ("07_C13_soft_lunch", frozenset({"C1", "C8", "C9", "C10", "C4", "C5", "C12", "C6", "C13"})),
    ("08_C15_full",       frozenset({"C1", "C8", "C9", "C10", "C4", "C5", "C12", "C6", "C13", "C15"})),
]


@dataclass
class StepResult:
    label:           str
    enabled:         FrozenSet[str]
    status:          str
    best_bis_str:    str      # HH:MM or "–"
    gap_pct:         float
    first_incumb_sec: float   # seconds to first incumbent; inf if none
    n_incumbents:    int
    conflicts:       int
    branches:        int
    wall_time_sec:   float
    n_booleans:      int      # booleans in internal CP-SAT model


# ── model builder ──────────────────────────────────────────────────────────────

def _build_and_solve(show, enabled: FrozenSet[str],
                     time_limit: float, label: str) -> StepResult:
    from ortools.sat.python import cp_model

    p     = show.params
    T0    = p.judging_start_slot
    T_min = p.slot_minutes

    segs    = show.segments
    seg_ids = [s.segment_id for s in segs]
    dur     = {s.segment_id: s.duration_slots for s in segs}

    ring_ids    = list(show.rings.keys())
    grp_rings   = set(show.group_rings)
    breed_rings = [r for r in ring_ids if r not in grp_rings]

    groups    = show.groups
    group_ids = list(groups.keys())
    judge_ids = list(show.judges.keys())

    judge_segs: Dict[str, List] = {jid: [] for jid in judge_ids}
    for s in segs:
        judge_segs[s.judge_id].append(s)

    D_BIS = math.ceil(20 / T_min)
    L     = p.lunch_duration_slots
    t_ls  = p.lunch_start_slot - T0
    t_le  = p.lunch_end_slot   - T0

    lunch_judges_set  = set(show.judges_requiring_lunch)
    soft_lunch_judges = [
        jid for jid in judge_ids
        if jid not in lunch_judges_set and len(judge_segs.get(jid, [])) >= 2
    ]

    # Horizon
    max_judge_load = max(
        (sum(s.duration_slots for s in judge_segs[jid])
         + (L if jid in lunch_judges_set else 0))
        for jid in judge_ids
    ) if judge_ids else 0
    T = max_judge_load + sum(g.judging_duration_slots for g in groups.values()) + D_BIS

    def _judge_lb(jid):
        return (sum(s.duration_slots for s in judge_segs[jid])
                + (L if jid in lunch_judges_set else 0))

    critical_lb = max(
        (_judge_lb(jid) for jid in judge_ids if judge_segs.get(jid)), default=0)
    if breed_rings:
        lb_par = math.ceil(sum(dur.values()) / len(breed_rings))
        critical_lb = max(critical_lb, lb_par)
    bis_lb = min(critical_lb, T - D_BIS)

    grp_feeding_segs: Dict[str, set] = {gid: set() for gid in group_ids}
    for seg in segs:
        for bid in seg.breed_ids:
            b = show.breeds.get(bid)
            if b and b.group_id in grp_feeding_segs:
                grp_feeding_segs[b.group_id].add(seg.segment_id)

    bis_jid   = show.bis_judge_id
    grp_judge = {gid: grp.judge_id for gid, grp in groups.items()}

    # ------------------------------------------------------------------
    # Build model
    # ------------------------------------------------------------------
    model = cp_model.CpModel()

    # Segment start / end
    start = {}
    end_s = {}
    for s in segs:
        sid = s.segment_id
        start[sid] = model.NewIntVar(0, T - dur[sid], f"start_{sid}")
        end_s[sid] = model.NewIntVar(dur[sid], T,     f"end_{sid}")
        model.Add(end_s[sid] == start[sid] + dur[sid])

    # Serial lower-bound hints (always — just domain tightening)
    for jid, jsegs in judge_segs.items():
        cumul = 0
        for seg in jsegs:
            if cumul > 0:
                model.Add(start[seg.segment_id] >= cumul)
            cumul += seg.duration_slots

    # Ring BoolVars + optional interval vars (always created)
    pres    = {sid: {} for sid in seg_ids}
    ring_iv = {sid: {} for sid in seg_ids}
    for sid in seg_ids:
        d = dur[sid]
        for rid in ring_ids:
            b = model.NewBoolVar(f"pres_{sid}_{rid}")
            pres[sid][rid] = b
            ring_iv[sid][rid] = model.NewOptionalIntervalVar(
                start[sid], d, end_s[sid], b, f"ringiv_{sid}_{rid}")
        if "C1" in enabled:
            model.AddExactlyOne(pres[sid][rid] for rid in ring_ids)

    # Group IntVars + interval vars (always created)
    tau_g  = {}
    end_g  = {}
    grp_iv = {}
    for gid, grp in groups.items():
        dg = grp.judging_duration_slots
        tau_g[gid] = model.NewIntVar(0, T - dg, f"tau_g_{gid}")
        end_g[gid] = model.NewIntVar(dg, T,      f"end_g_{gid}")
        model.Add(end_g[gid] == tau_g[gid] + dg)
        grp_iv[gid] = model.NewIntervalVar(tau_g[gid], dg, end_g[gid], f"grp_iv_{gid}")

    # BIS IntVar + interval var (always created)
    tau_bis = model.NewIntVar(max(bis_lb, 0), T - D_BIS, "tau_bis")
    end_bis = model.NewIntVar(D_BIS, T, "end_bis")
    model.Add(end_bis == tau_bis + D_BIS)
    bis_iv  = model.NewIntervalVar(tau_bis, D_BIS, end_bis, "bis_iv")

    # Required judge-axis interval vars for each segment (always created)
    judge_seg_iv = {}
    for s in segs:
        sid = s.segment_id
        judge_seg_iv[sid] = model.NewIntervalVar(
            start[sid], dur[sid], end_s[sid], f"judgeiv_{sid}")

    # Mandatory lunch vars (always created; constraint conditional on C12)
    lunch_start_var: Dict[str, object] = {}
    lunch_iv:        Dict[str, object] = {}
    for jid in lunch_judges_set:
        ls_v = model.NewIntVar(t_ls, t_le - L, f"lunchstart_{jid}")
        le_v = model.NewIntVar(t_ls + L, t_le,  f"lunchend_{jid}")
        model.Add(le_v == ls_v + L)
        lunch_start_var[jid] = ls_v
        lunch_iv[jid] = model.NewIntervalVar(ls_v, L, le_v, f"lunchiv_{jid}")

    # Soft lunch vars (always created; OnlyEnforceIf constraints conditional)
    sl_active:  Dict[str, Dict[int, object]] = {}
    sl_start_v: Dict[str, Dict[int, object]] = {}
    lunch_pen:  Dict[str, object] = {}
    for jid in soft_lunch_judges:
        jsegs  = judge_segs[jid]
        sl_active[jid]  = {}
        sl_start_v[jid] = {}
        for i in range(len(jsegs) - 1):
            sa     = jsegs[i]
            sb     = jsegs[i + 1]
            active = model.NewBoolVar(f"slactive_{jid}_{i}")
            sl_s   = model.NewIntVar(t_ls, t_le - L, f"slstart_{jid}_{i}")
            sl_e   = model.NewIntVar(t_ls + L, t_le,  f"slend_{jid}_{i}")
            model.Add(sl_e == sl_s + L)
            if "C13" in enabled:
                model.Add(sl_s >= end_s[sa.segment_id]).OnlyEnforceIf(active)
                model.Add(start[sb.segment_id] >= sl_e).OnlyEnforceIf(active)
            sl_active[jid][i]  = active
            sl_start_v[jid][i] = sl_s
        pen = model.NewBoolVar(f"lunchpen_{jid}")
        if "C13" in enabled:
            model.AddBoolOr(list(sl_active[jid].values()) + [pen])
        lunch_pen[jid] = pen

    # Ring-switch vars (always created; linking constraints conditional)
    switch_vars:  List = []
    for jid in judge_ids:
        jsegs = judge_segs[jid]
        for i in range(len(jsegs) - 1):
            sa = jsegs[i]
            sb = jsegs[i + 1]
            sw = model.NewBoolVar(f"sw_{jid}_{i}")
            switch_vars.append(sw)
            if "C15" in enabled:
                same_r = []
                for rid in ring_ids:
                    sr = model.NewBoolVar(f"samer_{jid}_{i}_{rid}")
                    model.Add(sr <= pres[sa.segment_id][rid])
                    model.Add(sr <= pres[sb.segment_id][rid])
                    same_r.append(sr)
                model.Add(sum(same_r) + sw == 1)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    # C4 — ring NoOverlap
    if "C4" in enabled:
        for rid in ring_ids:
            model.AddNoOverlap(ring_iv[sid][rid] for sid in seg_ids)

    # C5 + C11 + optional C12 — judge timeline NoOverlap
    if "C5" in enabled:
        for jid in judge_ids:
            jsegs    = judge_segs[jid]
            timeline = [judge_seg_iv[s.segment_id] for s in jsegs]
            for gid, gjid in grp_judge.items():
                if gjid == jid:
                    timeline.append(grp_iv[gid])
            if jid == bis_jid:
                timeline.append(bis_iv)
            if "C12" in enabled and jid in lunch_judges_set:
                timeline.append(lunch_iv[jid])
            if len(timeline) >= 2:
                model.AddNoOverlap(timeline)

    # C6 + C7 — arena NoOverlap
    if "C6" in enabled:
        arena_ivs = [grp_iv[gid] for gid in group_ids] + [bis_iv]
        for rid in grp_rings:
            for sid in seg_ids:
                arena_ivs.append(ring_iv[sid][rid])
        model.AddNoOverlap(arena_ivs)

    # C8 — BOB precedences
    if "C8" in enabled:
        for gid, sids in grp_feeding_segs.items():
            for sid in sids:
                model.Add(end_s[sid] <= tau_g[gid])

    # C9 — group → BIS precedences
    if "C9" in enabled:
        for gid in group_ids:
            model.Add(end_g[gid] <= tau_bis)

    # C10 — end of day
    if "C10" in enabled:
        for gid in group_ids:
            model.Add(end_g[gid] <= T)
        model.Add(end_bis <= T)

    # ------------------------------------------------------------------
    # Objective — always minimise tau_bis; add friction terms when enabled
    # ------------------------------------------------------------------
    L3_max = len(switch_vars) + len(soft_lunch_judges) + 1
    w_L1   = L3_max + 1

    obj = w_L1 * tau_bis
    if "C15" in enabled:
        obj = obj + sum(switch_vars)
    if "C13" in enabled:
        obj = obj + sum(lunch_pen.values())
    model.Minimize(obj)

    model.AddDecisionStrategy(
        [tau_bis], cp_model.CHOOSE_FIRST, cp_model.SELECT_MIN_VALUE)

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    t0     = time.time()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.log_search_progress = False   # suppress per-step spam

    first_incumb_sec_box = [float("inf")]
    n_incumbents_box     = [0]

    class _Cb(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            n_incumbents_box[0] += 1
            if math.isinf(first_incumb_sec_box[0]):
                first_incumb_sec_box[0] = time.time() - t0

    status_code = solver.Solve(model, _Cb())
    wall        = time.time() - t0

    status_name = solver.StatusName(status_code)
    if status_name in ("OPTIMAL", "FEASIBLE"):
        obj_val  = solver.ObjectiveValue()
        bound    = solver.BestObjectiveBound()
        tau_val  = int(obj_val // w_L1)
        bis_str  = p.slot_to_hhmm(T0 + tau_val)
        gap_pct  = max(0.0, (obj_val - bound) / obj_val * 100) if obj_val > 1e-6 else 0.0
    else:
        bis_str = "–"
        gap_pct = 100.0

    # n_booleans from response proto
    try:
        n_bools = solver.ResponseProto().num_booleans
    except Exception:
        n_bools = 0

    return StepResult(
        label            = label,
        enabled          = enabled,
        status           = status_name,
        best_bis_str     = bis_str,
        gap_pct          = gap_pct,
        first_incumb_sec = first_incumb_sec_box[0],
        n_incumbents     = n_incumbents_box[0],
        conflicts        = solver.NumConflicts(),
        branches         = solver.NumBranches(),
        wall_time_sec    = wall,
        n_booleans       = n_bools,
    )


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Incremental CP-SAT constraint benchmark for AKC scheduling.")
    parser.add_argument("show_file")
    parser.add_argument("--time",         type=float, default=30,
                        help="Seconds per step (default 30)")
    parser.add_argument("--slot-minutes", type=int,   default=None,
                        dest="slot_minutes")
    parser.add_argument("--steps",        default=None,
                        help="Comma-separated step labels to run (default: all)")
    args = parser.parse_args()

    from akc_preprocessing import load_show
    print(f"Loading {args.show_file} ...", file=sys.stderr)
    show = load_show(args.show_file, slot_minutes=args.slot_minutes)

    steps = STEPS
    if args.steps:
        wanted = set(args.steps.split(","))
        steps  = [(lbl, en) for lbl, en in STEPS if lbl in wanted]

    results: List[StepResult] = []
    for label, enabled in steps:
        added = sorted(enabled - (results[-1].enabled if results else frozenset()))
        added_str = f"+{{{', '.join(added)}}}" if added else "(none)"
        print(f"\n{'─'*60}", file=sys.stderr)
        print(f"Step {label}  adding {added_str}", file=sys.stderr)
        print(f"{'─'*60}", file=sys.stderr)
        r = _build_and_solve(show, enabled, args.time, label)
        results.append(r)
        fi = f"{r.first_incumb_sec:.1f}s" if not math.isinf(r.first_incumb_sec) else "none"
        cb_ratio = f"{r.conflicts/r.branches:.2f}" if r.branches else "–"
        print(f"  → {r.status:<10}  BIS {r.best_bis_str}  gap {r.gap_pct:.1f}%"
              f"  first {fi}  incumbents {r.n_incumbents}"
              f"  conflicts {r.conflicts:,}  branches {r.branches:,}"
              f"  c/b {cb_ratio}",
              file=sys.stderr)

    # ------------------------------------------------------------------
    # Comparison table
    # ------------------------------------------------------------------
    print()
    print("=" * 100)
    print("CONSTRAINT BENCHMARK SUMMARY")
    print("=" * 100)
    hdr = (f"{'Step':<22}  {'Status':<10}  {'BIS':>7}  {'gap%':>6}"
           f"  {'1st(s)':>6}  {'#sol':>4}  {'conflicts':>10}"
           f"  {'branches':>10}  {'c/b':>5}  {'bools':>7}")
    print(hdr)
    print("-" * 100)
    for r in results:
        fi    = f"{r.first_incumb_sec:.1f}" if not math.isinf(r.first_incumb_sec) else "none"
        ratio = f"{r.conflicts/r.branches:.2f}" if r.branches else "–"
        print(f"{r.label:<22}  {r.status:<10}  {r.best_bis_str:>7}  {r.gap_pct:>5.1f}%"
              f"  {fi:>6}  {r.n_incumbents:>4}  {r.conflicts:>10,}"
              f"  {r.branches:>10,}  {ratio:>5}  {r.n_booleans:>7,}")
    print("=" * 100)


if __name__ == "__main__":
    _cli()
