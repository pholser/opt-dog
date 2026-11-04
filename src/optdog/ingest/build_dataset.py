"""Parse the raw judging programs listed in each manifest into data/parsed/.

    python -m optdog.ingest.build_dataset
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from optdog.domain import fmt_time
from optdog.ingest.bradshaw import BradshawParser, ParsedDay
from optdog.io.program_csv import write_program

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
PARSED = ROOT / "data" / "parsed"


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
        f"- judging starts: {fmt_time(show.start) if show.start else '?'}",
    ]
    if plan:
        lines.append(
            f"- groups: {fmt_time(plan.start) if plan.start else '?'} in rings "
            f"{', '.join(str(x.number) for x in plan.rings)}; order: {', '.join(g.value for g in plan.order)}; "
            f"BIS: {plan.bis_judge.name if plan.bis_judge else '?'}"
        )
    lines += ["", "## Checks", "", "| check | stated | parsed | |", "|---|---|---|---|"]
    for what, stated, parsed in r.checks:
        lines.append(f"| {what} | {stated} | {parsed} | {'ok' if stated == parsed else '**MISMATCH**'} |")
    for title, items in (
        ("Breed index mismatches", r.index_mismatches),
        ("Unknown breeds", r.unknown_breeds),
        ("Unparsed lines", r.unparsed),
        ("Notes", r.notes),
    ):
        if items:
            lines += ["", f"## {title}", ""] + [f"- {x}" for x in items]
    path.write_text("\n".join(lines) + "\n")


def build_bradshaw() -> list[tuple[str, ParsedDay]]:
    parser = BradshawParser()
    src = RAW / "bradshaw"
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
            key = f"{e['date']}-{slug(e['show'])}"
            d = PARSED / "bradshaw" / key
            write_program(pd.program, d, source=f"bradshaw/{fname}#{e['day']}")
            write_report(pd, d / "report.md")
            out.append((key, pd))
    return out


def main() -> None:
    results = build_bradshaw()
    print(f"{'show':55} {'checks':>9} {'index':>9}")
    for key, pd in results:
        r = pd.report
        idx = next((c for c in r.checks if c[0].startswith("breed index")), None)
        ok = len(r.checks) - len(r.failed_checks)
        print(f"{key:55} {ok:>4}/{len(r.checks):<4} {(f'{idx[2]}/{idx[1]}' if idx else '-'):>9}")


if __name__ == "__main__":
    main()
