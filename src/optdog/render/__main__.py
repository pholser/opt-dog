"""Render a parsed judging program, and optionally the greedy schedule for the same show.

    python -m optdog.render data/parsed/bradshaw/2026-09-12-sir-francis-drake-kennel-club
    python -m optdog.render <show dir> --greedy --html schedule.html
    python -m optdog.render <show dir> --greedy-only --text

With --html, writes the chart; otherwise (or with --text too) prints the text program.
Exhibitor conflicts are scored when synthetic entries exist (data/synthetic/..., --seed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from optdog.instance import instance_from_program
from optdog.io.program_csv import read_entries, read_program
from optdog.params import ShowParameters
from optdog.render.html import render_html
from optdog.render.text import render_text
from optdog.solve.greedy import greedy_program

ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optdog.render", description=__doc__.split("\n")[0])
    ap.add_argument("show_dir", type=Path, help="a parsed program directory (data/parsed/<source>/<show>)")
    ap.add_argument("--greedy", action="store_true", help="also render the greedy schedule for the same show")
    ap.add_argument("--greedy-only", action="store_true", help="render only the greedy schedule")
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

    if a.html:
        a.html.write_text(render_html(programs, params, title=f"{human.show.name} — {human.show.date}"))
        print(f"wrote {a.html}", file=sys.stderr)
    if a.text or not a.html:
        for label, prog in programs:
            print(render_text(prog, params, title=f"{label}: {human.show.name} — {human.show.date}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
