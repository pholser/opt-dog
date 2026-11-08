"""Render a parsed judging program, and optionally the greedy schedule for the same show.

    python -m optdog.render data/parsed/bradshaw/2026-09-12-sir-francis-drake-kennel-club
    python -m optdog.render <show dir> --greedy --html schedule.html
    python -m optdog.render <show dir> --greedy-only --text
    python -m optdog.render <show dir> --solve --compare          # published vs 3c, side by side
    python -m optdog.render <show dir> --solve formation --compare ring --out cmp.txt
    python -m optdog.render <show dir> --saved --compare          # the same, from data/solved

With --html, writes the chart; with --compare, prints the first and last program side by
side (aligned by judge, or by ring); otherwise (or with --text too) prints the text program.
Exhibitor conflicts are scored when synthetic entries exist (data/synthetic/..., --seed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from optdog.instance import instance_from_program
from optdog.io.program_csv import read_entries, read_program
from optdog.params import ShowParameters
from optdog.render.compare import render_compare
from optdog.render.html import render_html
from optdog.render.text import render_text
from optdog.solve.greedy import greedy_program

ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optdog.render", description=__doc__.split("\n")[0])
    ap.add_argument("show_dir", type=Path, help="a parsed program directory (data/parsed/<source>/<show>)")
    ap.add_argument("--greedy", action="store_true", help="also render the greedy schedule for the same show")
    ap.add_argument("--greedy-only", action="store_true", help="render only the greedy schedule")
    ap.add_argument("--cp", action="store_true", help="also render the CP-SAT placement (same as --solve placement)")
    ap.add_argument("--solve", nargs="?", const="packing", choices=["placement", "formation", "ordering", "packing"],
                    help="also render a CP-SAT schedule: step 1 placement, 3a formation, or 3b ordering (default)")
    ap.add_argument("--saved", nargs="?", const="packing", choices=["placement", "formation", "ordering", "packing"],
                    help="also render a saved schedule from data/solved instead of solving")
    ap.add_argument("--compare", nargs="?", const="judge", choices=["judge", "ring"],
                    help="print the first and last program side by side, aligned by judge (default) or ring")
    ap.add_argument("--width", type=int, default=76, help="column width for --compare")
    ap.add_argument("--out", type=Path, help="write the text output to this file instead of stdout")
    ap.add_argument("--time-limit", type=float, default=60.0, help="CP-SAT seconds per phase")
    ap.add_argument("--html", type=Path, help="write an HTML chart to this file")
    ap.add_argument("--text", action="store_true", help="print the text program (default without --html)")
    ap.add_argument("--seed", type=int, default=0, help="synthetic-exhibitor seed for conflict scoring")
    a = ap.parse_args(argv)

    human = read_program(a.show_dir)
    synth = ROOT / "data" / "synthetic" / a.show_dir.parent.name / a.show_dir.name / f"seed-{a.seed}"
    if synth.exists():
        human.show.entries = read_entries(synth, human)
    params = ShowParameters()
    programs = []
    if not a.greedy_only:
        programs.append(("Published program", human))
    if a.greedy or a.greedy_only:
        programs.append(("Greedy baseline", greedy_program(instance_from_program(human), params)))
    if a.saved:
        from optdog.solutions import load_result, solved_dir

        res = load_result(solved_dir(a.show_dir) / a.saved, instance_from_program(human))
        status = res.status.lower()
        programs.append((f"CP-SAT {a.saved} ({status})", res.program))
    step = "placement" if a.cp and not a.solve else a.solve
    if step:
        from optdog.solve.cpsat import PlacementOptions, solve_formation, solve_ordering, solve_packing, solve_placement

        solve, name = {"placement": (solve_placement, "CP-SAT placement"),
                       "formation": (solve_formation, "CP-SAT 3a"),
                       "ordering": (solve_ordering, "CP-SAT 3b"),
                       "packing": (solve_packing, "CP-SAT 3c")}[step]
        print(f"solving ({name}, up to {a.time_limit:.0f} s per phase)...", file=sys.stderr)
        res = solve(instance_from_program(human), params, options=PlacementOptions(time_limit=a.time_limit))
        if res.program is None:
            print(f"CP-SAT found no schedule: {res.phases}", file=sys.stderr)
        else:
            status = res.status.lower()
            programs.append((f"{name} ({status})", res.program))

    if a.html:
        a.html.write_text(render_html(programs, params, title=f"{human.show.name} — {human.show.date}"))
        print(f"wrote {a.html}", file=sys.stderr)
    text = []
    if a.compare:
        if len(programs) < 2:
            ap.error("--compare needs two programs: add --solve, --cp or --greedy")
        text.append(render_compare(programs[0], programs[-1], params, by=a.compare, width=a.width,
                                   title=f"{human.show.name} — {human.show.date}"))
    elif a.text or not a.html:
        for label, prog in programs:
            text.append(render_text(prog, params, title=f"{label}: {human.show.name} — {human.show.date}"))
    if text:
        if a.out:
            a.out.write_text("\n".join(text))
            print(f"wrote {a.out}", file=sys.stderr)
        else:
            print("\n".join(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
