"""Parse the raw judging programs listed in each superintendent's manifest into data/parsed/.

    python -m optdog.ingest.build_dataset            # every superintendent with a parser
    python -m optdog.ingest.build_dataset rau        # only these

Each superintendent has a parser (``PARSERS``) and ``data/raw/<source>/manifest.csv``: one row
per show day with its file, the day as the program names it, the club, the date, whether to
include it, and notes.

Only regular breed events of the all-breed show are kept (U 2026-11-13): the parser reads
sweepstakes, Junior Showmanship, Beginner Puppy, FSS open shows, concurrent specialties, obedience
and rally, for its checks against the program's printed totals and breed index, but keeps none of
them, and each report says what there was.
"""

from __future__ import annotations

import csv
import re
import sys
from collections.abc import Callable
from pathlib import Path

from optdog.ingest.bradshaw import BradshawParser
from optdog.ingest.common import ParsedDay
from optdog.ingest.rau import RauParser
from optdog.io.csv_dataset import CsvDataset
from optdog.sources import ShowKey

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"

# superintendent -> its parser: ``parse(pdf)`` gives every all-breed day in the file
PARSERS: dict[str, Callable[[], BradshawParser | RauParser]] = {"bradshaw": BradshawParser, "rau": RauParser}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def write_report(pd: ParsedDay, path: Path) -> None:
    r, prog = pd.report, pd.program
    show, plan = prog.show, prog.groups
    lines = [
        f"# {show.name} — {show.date}",
        "",
        f"Source: `{Path(r.source).relative_to(ROOT)}` ({r.day})",
        "",
        f"- judges: {len(show.judges)}, rings: {len(show.rings)}, blocks: {len(prog.blocks)}, events: {len(show.events)}",
        f"- judging starts: {show.start}",
    ]
    if plan:
        lines.append(
            f"- groups: {plan.start if plan.start is not None else '?'} in rings "
            f"{', '.join(str(x.number) for x in plan.rings)}; order: {', '.join(g.value for g in plan.order)}; "
            f"BIS: {plan.bis_judge.name if plan.bis_judge else '?'}"
        )
    lines += ["", "## Checks", "", "| check | stated | parsed | |", "|---|---|---|---|"]
    for what, stated, parsed in r.checks:
        lines.append(f"| {what} | {stated} | {parsed} | {'ok' if stated == parsed else '**MISMATCH**'} |")
    for title, items in (
        ("Not kept (only regular breed events are)", r.not_kept),
        ("Breed index mismatches", r.index_mismatches),
        ("Unknown breeds", r.unknown_breeds),
        ("Unparsed lines", r.unparsed),
        ("Notes", r.notes),
    ):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {x}" for x in items]
    path.write_text("\n".join(lines) + "\n")


def build(source: str) -> list[tuple[str, ParsedDay]]:
    parser = PARSERS[source]()
    data = CsvDataset()
    src = RAW / source
    with open(src / "manifest.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["include"] == "1"]
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    out = []
    for fname, entries in by_file.items():
        days = {pd.report.day: pd for pd in parser.parse(src / fname)}
        for e in entries:
            pd = days.get(e["day"])
            if pd is None:
                raise SystemExit(f"{fname}: no all-breed show parsed for {e['day']}")
            pd.program.show.name = e["show"]
            pd.program.show.date = e["date"]
            if e["notes"]:
                pd.report.notes.append(f"manifest: {e['notes']}")
            key = ShowKey(source, f"{e['date']}-{slug(e['show'])}")
            data.put_program(key, pd.program, origin=f"{source}/{fname}#{e['day']}")
            write_report(pd, data.program_dir(key) / "report.md")
            out.append((key.slug, pd))
    return out


def main(argv: list[str] | None = None) -> None:
    sources = (sys.argv[1:] if argv is None else argv) or list(PARSERS)
    results = [(f"{s}/{key}", pd) for s in sources for key, pd in build(s)]
    print(f"{'show':62} {'checks':>9} {'index':>9}")
    for key, pd in results:
        r = pd.report
        idx = next((c for c in r.checks if c[0].startswith("breed index")), None)
        ok = len(r.checks) - len(r.failed_checks)
        print(f"{key:62} {ok:>4}/{len(r.checks):<4} {(f'{idx[2]}/{idx[1]}' if idx else '-'):>9}")


if __name__ == "__main__":
    main()
