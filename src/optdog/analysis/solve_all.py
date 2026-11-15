"""Solve every parsed show and save the results, for the baseline report and the figures.

    python -m optdog.analysis.solve_all                  # all days, all steps
    python -m optdog.analysis.solve_all --only 2026-01   # days whose name contains this
    python -m optdog.analysis.solve_all --step packing   # this step only, keeping the rest

Writes data/solved (see io/csv_dataset.py). Every saved schedule is checked for hard-constraint
violations first; V1 (a judge over the entry limit) is an input problem and is allowed.
"""

from __future__ import annotations

import argparse
import sys
import time

from optdog.io.csv_dataset import CsvDataset
from optdog.params import ShowParameters
from optdog.sources import STEPS, published_day
from optdog.solve.cpsat import NoSchedule, PlacementOptions, solve_formation, solve_ordering, solve_packing, solve_placement
from optdog.solve.relaxation import relaxed_lower_bound
from optdog.validate import validate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optdog.analysis.solve_all", description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="only days whose directory name contains this")
    ap.add_argument("--step", choices=STEPS, help="solve only this step and keep the saved bound and other steps")
    ap.add_argument("--time-limit", type=float, default=60.0, help="CP-SAT seconds per phase")
    a = ap.parse_args(argv)
    params = ShowParameters()
    data = CsvDataset()
    bad = 0
    for key in data.program_keys():
        if a.only and a.only not in key.slug:
            continue
        _, show = published_day(data, data, key)
        t = time.perf_counter()
        line = [key.slug + ":"]
        if not a.step:
            bound = relaxed_lower_bound(show, params, time_limit=a.time_limit)
            data.put_bound(key, bound)
            line.append(f"bound {bound.value.minutes:.1f} ({bound.status.lower()})")
        for step, solve, options in (
            ("placement", solve_placement, PlacementOptions(time_limit=30, tidy=False)),
            ("formation", solve_formation, PlacementOptions(time_limit=a.time_limit)),
            ("ordering", solve_ordering, PlacementOptions(time_limit=a.time_limit)),
            ("packing", solve_packing, PlacementOptions(time_limit=a.time_limit)),
        ):
            if a.step and step != a.step:
                continue
            try:
                res = solve(show, params, options=options)
            except NoSchedule:
                line.append(f"{step} found no schedule")
                bad += 1
                continue
            hard = sorted({v.code for v in validate(res.program, params).violations} - {"V1"})
            if hard:
                line.append(f"{step} NOT SAVED: {hard}")
                bad += 1
                continue
            data.put_result(key, step, res)
            line.append(f"{step} {res.finish.minutes:.1f} ({res.status.lower()})")
        print(f"{'; '.join(line)}  [{time.perf_counter() - t:.0f} s]", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
