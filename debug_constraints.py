"""
debug_constraints.py — incremental constraint-family feasibility tester

Usage:
    python debug_constraints.py [FAMILIES...] [--model FILE] [--time N] [--verbose]

FAMILIES can be any subset of: C1 C2 C4 C5 C6 C7 C8 C9 C10 C11 C12 C13 C15 C16 ALL

Example:
    python debug_constraints.py C1 C2 C5 C12        # test just these families
    python debug_constraints.py ALL --model small.xlsx
"""

import argparse
import sys
import pyomo.environ as pyo

# Map family names to actual Pyomo constraint component names in akc_mip2
FAMILIES = {
    "C1":  {"C1"},
    "C2":  {"C2"},
    "C4":  {"C4a", "C4b"},
    "C5":  {"C5a", "C5b"},
    "C6":  {"C6_fwd", "C6_rev"},
    "C7":  {"C7a", "C7b"},
    "C8":  {"C8"},
    "C9":  {"C9"},
    "C10": {"C10_bis", "C10_grp"},
    "C11": {"C11a", "C11b", "C11c", "C11d"},
    "C12": {"C12a", "C12b", "C12c", "C12e"},
    "C13": {"C13a", "C13b", "C13c"},
    "C15": {"C15a", "C15b"},
    "C16": {"C16a", "C16b"},
}

ALL_FAMILIES = set().union(*FAMILIES.values())


def run(families_to_enable, model_file, time_limit, verbose, solver="scip", no_tau_lb=False):
    from akc_preprocessing import load_show
    from akc_mip2 import _build_model, _compute_greedy_warmstart, _solve_scip, _solve_pyomo, SolveParams
    import time

    print(f"Loading {model_file} ...", file=sys.stderr)
    show = load_show(model_file)

    print("Building model ...", file=sys.stderr)
    model = _build_model(show, show.params)

    # Collect all constraint names actually present in the model
    all_con_names = set()
    for con in model.component_objects(pyo.Constraint, active=True):
        all_con_names.add(con.name)

    if verbose:
        print(f"All constraint names in model: {sorted(all_con_names)}", file=sys.stderr)

    # Deactivate everything not in families_to_enable
    for con in model.component_objects(pyo.Constraint, active=True):
        if con.name not in families_to_enable:
            con.deactivate()

    active = sorted(c.name for c in model.component_objects(pyo.Constraint, active=True))
    print(f"Active constraints: {active}", file=sys.stderr)

    # Try warm start
    ws = []
    try:
        ws = _compute_greedy_warmstart(model, show)
        print(f"Warm start: {len(ws)} assignments", file=sys.stderr)
    except Exception as e:
        print(f"Warm start failed: {e}", file=sys.stderr)

    params = SolveParams(time_limit_sec=time_limit, mip_gap=0.01, solver=solver)
    t0 = time.time()
    if solver.lower() == "scip":
        result = _solve_scip(model, ws, show, params, t0)
    else:
        result = _solve_pyomo(model, ws, show, params, t0)
    print(result.summary(), file=sys.stderr)

    if result.status.upper() in ("OPTIMAL", "FEASIBLE"):
        print("\n=== FEASIBLE SOLUTION FOUND ===", file=sys.stderr)
        from akc_program import print_program
        print_program(result)
    else:
        print(f"\n=== NO FEASIBLE SOLUTION (status={result.status}) ===", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("families", nargs="*", default=["ALL"],
                        help="Families to activate (e.g. C1 C2 C12), or ALL")
    parser.add_argument("--model",  default="medium.xlsx")
    parser.add_argument("--time",   type=int, default=60)
    parser.add_argument("--solver", default="scip",
                        help="Solver to use: scip (default), highs, cbc, glpk")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-tau-lb", action="store_true")
    args = parser.parse_args()

    # Expand ALL
    if "ALL" in args.families:
        to_enable = set(ALL_FAMILIES)
    else:
        to_enable = set()
        for f in args.families:
            if f in FAMILIES:
                to_enable |= FAMILIES[f]
            else:
                print(f"Unknown family {f!r}. Known: {sorted(FAMILIES)}", file=sys.stderr)
                sys.exit(1)

    run(to_enable, args.model, args.time, args.verbose, args.solver, args.no_tau_lb)


if __name__ == "__main__":
    main()
