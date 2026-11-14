"""Plan a show from scratch: from a panel, entries and rings to a judging program.

    python -m optdog.plan check <problem dir>
    python -m optdog.plan solve <problem dir> --out <dir> [--html chart.html] [--time-limit 60]
    python -m optdog.plan extract <parsed program dir> <problem dir> [--entries <dir>]

``extract`` writes a published show's inputs; ``--entries`` adds exhibitors and entries from a
directory with ``exhibitors.csv`` and ``entries.csv`` (e.g. data/synthetic/<source>/<show>/seed-0).

The problem format is described in io/problem_csv.py. ``check`` reports anything that would
stop a valid program before any solving; ``solve`` runs step 3c, validates the result and
writes the program in the same CSV format as a parsed one.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from optdog.conflicts import exhibitor_conflicts
from optdog.domain import VARIETY_GROUPS, Show, fmt_time
from optdog.instance import instance_from_program
from optdog.io.problem_csv import ProblemFormatError, read_problem, write_problem
from optdog.io.program_csv import read_entries, read_program, write_program
from optdog.params import ShowParameters
from optdog.render.html import render_html
from optdog.render.text import render_text
from optdog.solve.cpsat import PlacementOptions, solve_packing
from optdog.validate import validate


@dataclass(frozen=True)
class Issue:
    error: bool  # an error stops a valid program; a warning is for the superintendent to review
    where: str
    what: str

    def __str__(self) -> str:
        return f"{'ERROR' if self.error else 'warning'}  {self.where}: {self.what}"


def check_problem(show: Show, params: ShowParameters | None = None) -> list[Issue]:
    """What is wrong with the inputs themselves, whatever the schedule."""
    p = (params or ShowParameters()).for_show(show)
    out: list[Issue] = []

    def error(where: str, what: str) -> None:
        out.append(Issue(True, where, what))

    def warn(where: str, what: str) -> None:
        out.append(Issue(False, where, what))

    if show.start is None:
        error("show", "no judging start time")
    if not show.rings:
        error("rings", "no rings")
    if not show.group_rings:
        error("rings", "no group ring")
    for r in show.group_rings:
        if r.size < p.group_ring_min:
            error(str(r), f"group ring is {r.size.name}; needs {p.group_ring_min.name} (H17)")
    a, b = p.lunch_window
    if a >= b:
        error("show", f"lunch window {fmt_time(a)}–{fmt_time(b)} is empty")

    for g in VARIETY_GROUPS:
        if show.group_entrants(g) > 0 and g not in show.group_judges:
            error(g.value, "breeds are entered but the group has no judge")
    if show.bis_judge is None:
        error("show", "no Best in Show judge")

    largest = max((r.size for r in show.rings), default=None)
    pinned = {id(e) for sb in show.pinned for e in sb.block.events}
    if largest is not None:
        for e in show.events:
            if id(e) not in pinned and e.min_ring > largest:
                error(e.name, f"needs a {e.min_ring.name} ring; the largest is {largest.name} (H16)")
    for e in show.events:
        if e.entries < 0:
            error(e.name, f"{e.entries} entries")

    for j in show.judges:
        load = show.judge_load(j)
        if load > p.judge_daily_limit:
            warn(j.name, f"{load} entries; the limit is {p.judge_daily_limit} a day (V1)")

    seen: dict[tuple, int] = defaultdict(int)
    for e in show.events:
        seen[e.judge.name, e.name, e.competition.value] += 1
    for (judge, name, comp), n in seen.items():
        if n > 1:
            warn(name, f"listed {n} times for {judge} ({comp})")

    # fixed commitments that cannot both happen
    def clashes(key, label: str) -> None:
        groups = defaultdict(list)
        for sb in show.pinned:
            groups[key(sb)].append(sb)
        for k, seq in groups.items():
            seq.sort(key=lambda sb: sb.start)
            for x, y in zip(seq, seq[1:]):
                if y.start < x.end - p.overlap_tolerance_minutes:
                    error(label.format(k), f"pinned blocks overlap at {fmt_time(y.start)}")

    clashes(lambda sb: sb.ring.number, "Ring {}")
    clashes(lambda sb: sb.judge.name, "{}")

    # exhibitors and entries (optional)
    if show.entries:
        rows: dict[int, int] = defaultdict(int)
        dogs: dict[tuple[str, int], int] = defaultdict(int)
        for en in show.entries:
            rows[id(en.event)] += 1
            dogs[en.dog, id(en.event)] += 1
        for e in show.events:
            if rows[id(e)] and rows[id(e)] != e.entries:
                warn(e.name, f"{rows[id(e)]} rows in entries.csv for {e.entries} entries")
        names = {id(e): e.name for e in show.events}
        for (dog, ev), n in dogs.items():
            if n > 1:
                warn(names[ev], f"dog {dog} is entered {n} times")
        if not any(en.exhibitor.registered for en in show.entries):
            warn("exhibitors.csv", "no exhibitor is signed up for conflict resolution, so conflicts are not scheduled")
    return out


def solve(show: Show, params: ShowParameters | None = None, time_limit: float = 60.0):
    return solve_packing(show, params, PlacementOptions(time_limit=time_limit))


def _report(issues: list[Issue]) -> bool:
    for i in sorted(issues, key=lambda i: not i.error):
        print(i, file=sys.stderr)
    return any(i.error for i in issues)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optdog.plan", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="report problems with the inputs")
    c.add_argument("problem", type=Path)
    s = sub.add_parser("solve", help="generate a judging program")
    s.add_argument("problem", type=Path)
    s.add_argument("--out", type=Path, help="write the program here as CSV")
    s.add_argument("--html", type=Path, help="write a chart of the program")
    s.add_argument("--time-limit", type=float, default=60.0, help="CP-SAT seconds per phase")
    e = sub.add_parser("extract", help="write a published program's inputs as a problem")
    e.add_argument("program", type=Path)
    e.add_argument("problem", type=Path)
    e.add_argument("--entries", type=Path, help="a directory with exhibitors.csv and entries.csv for this show")
    a = ap.parse_args(argv)

    if a.command == "extract":
        program = read_program(a.program)
        if a.entries:
            program.show.entries = read_entries(a.entries, program)
        write_problem(instance_from_program(program), a.problem)
        print(f"wrote {a.problem}", file=sys.stderr)
        return 0

    params = ShowParameters()
    try:
        show = read_problem(a.problem)
    except (ProblemFormatError, FileNotFoundError, KeyError, ValueError) as err:
        print(f"ERROR  cannot read {a.problem}: {err}", file=sys.stderr)
        return 2
    if _report(check_problem(show, params)):
        return 2
    if a.command == "check":
        print("no errors", file=sys.stderr)
        return 0

    print(f"solving (up to {a.time_limit:.0f} s per phase, {3 * a.time_limit:.0f} s for idle and conflicts)...", file=sys.stderr)
    res = solve(show, params, a.time_limit)
    if res.program is None:
        print(f"no schedule found: {[(ph.name, ph.status) for ph in res.phases]}", file=sys.stderr)
        return 1
    hard = [v for v in validate(res.program, params).violations if v.code != "V1"]
    if hard:
        for v in hard:
            print(f"✗ {v}", file=sys.stderr)
        print("the schedule breaks hard rules: not written", file=sys.stderr)
        return 1
    print(f"finish {fmt_time(int(round(res.finish)))} ({res.status.lower()})", file=sys.stderr)
    registered = {en.exhibitor.id for en in show.entries if en.exhibitor.registered}
    if registered:
        report = exhibitor_conflicts(res.program, params.exhibitor_travel_minutes)
        print(f"exhibitor conflicts: {report.minutes:.0f} min, "
              f"{len(report.by_exhibitor())} of {len(registered)} registered exhibitors", file=sys.stderr)
    if a.out:
        write_program(res.program, a.out, source="optdog.plan")
        print(f"wrote {a.out}", file=sys.stderr)
    if a.html:
        a.html.write_text(render_html([("Generated", res.program)], params, title=f"{show.name} — {show.date}"))
        print(f"wrote {a.html}", file=sys.stderr)
    if not a.out and not a.html:
        print(render_text(res.program))
    return 0


if __name__ == "__main__":
    sys.exit(main())
