"""The Rau Dog Shows parser, on the programs in data/raw/rau."""

import csv
from pathlib import Path

import pytest

from optdog.domain import VARIETY_GROUPS, TimeOfDay
from optdog.ingest.common import clock, norm_name
from optdog.ingest.rau import RauParser
from optdog.io.program_csv import read_program, write_program

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "rau"

# Mismatches traced to the programs themselves: their figures disagree with each other.
KNOWN_SOURCE_ERRORS = {
    # a breakdown plus its "Veteran" lines comes to one more than the printed entries
    *(("Twin-Brooks-2024-JP-Mon.pdf", f"breakdown and non-regular classes {b}")
      for b in ("Doberman Pinschers", "Newfoundlands", "Bulldogs", "Dalmatians", "Schipperkes", "Dachshunds (Longhaired)")),
    ("KC-Anne-Arundel-Carroll-KC-2024-JP-Fri.pdf", "breakdown and non-regular classes Keeshonden"),  # 1-6-(2-1) for 9 dogs
    ("KC-Anne-Arundel-Carroll-KC-2024-JP-Fri.pdf", "Mrs. Betty Nelson Pollock total dogs"),  # 37 printed; her listings add to 74
    # the index gives Bernese at 10:00, when that ring's sweepstakes start; breed judging is at 10:30
    ("KC-Anne-Arundel-Carroll-KC-2024-JP-Fri.pdf", "breed index rows (breeds) matching listings"),
}


def _manifest(include: str = "1"):
    with open(RAW / "manifest.csv", newline="") as f:
        return [r for r in csv.DictReader(f) if r["include"] == include]


@pytest.fixture(scope="module")
def parsed():
    parser = RauParser()
    return {r["file"]: parser.parse(RAW / r["file"])[0] for r in _manifest()}


@pytest.mark.parametrize("text, expected", [
    ("6:30 PM", TimeOfDay.at(18, 30)), ("1:00 P.M.", TimeOfDay.at(13)), ("10:30 A.M.", TimeOfDay.at(10, 30)),
    ("12:00 NOON", TimeOfDay.at(12)), ("LUNCH", None),
])
def test_clock(text, expected):
    assert clock(text) == expected


def test_judge_names_match_across_the_program():
    assert norm_name("Mr. James R Dickson") == norm_name("James R. Dickson")
    assert norm_name("Ms. Peggy Beisel-McIlwaine") == norm_name("Peggy Beisel-McIlwaine")


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: r["file"])
def test_the_programs_own_figures_agree_with_the_parse(parsed, row):
    pd = parsed[row["file"]]
    bad = [c for c in pd.report.failed_checks if (row["file"], c[0]) not in KNOWN_SOURCE_ERRORS]
    assert not bad, bad
    assert not pd.report.unknown_breeds
    assert {c[0] for c in pd.report.checks} >= {"breeds or varieties", "breed index rows (breeds) matching listings",
                                                 "breed judging start, minutes after midnight"}


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: r["file"])
def test_the_day_is_complete(parsed, row):
    prog = parsed[row["file"]].program
    plan = prog.groups
    assert plan is not None and plan.bis_judge is not None
    assert sorted(g.value for g in plan.order) == sorted(g.value for g in VARIETY_GROUPS)
    assert len(plan.rings) == 2 and plan.start is not None
    assert all(j in prog.show.judges for j in prog.show.group_judges.values())  # group judges are breed judges here
    assert all(e.breed is not None for e in prog.show.events)
    assert all(e.counts is None or e.counts.total == e.entries for e in prog.show.events)
    assert prog.show.closing_time is not None and prog.show.closing_time > plan.start


def test_what_is_not_breed_judging_is_left_out(parsed):
    pd = parsed["KC-Anne-Arundel-Carroll-KC-2024-JP-Fri.pdf"]
    names = {e.name for e in pd.program.show.events}
    assert "Dutch Shepherds" in names and "Miscellaneous Classes" not in names  # the heading is not an event
    assert not any("Puppies" in n or "Junior" in n for n in names)
    bernese = [e for e in pd.program.show.events if e.name == "Bernese Mountain Dogs"]
    assert [e.entries for e in bernese] == [40]  # the 12-dog sweepstakes is not breed judging
    assert any("Beginner Puppy" in x for x in pd.report.not_kept)


def test_veteran_classes_are_the_non_regular_entries(parsed):
    events = {e.name: e for e in parsed["Twin-Brooks-2024-JP-Mon.pdf"].program.show.events}
    boxers = events["Boxers"]  # "8 - Boxers 4-1-(0-2)" and "1 - Veteran Dog"
    assert (boxers.entries, boxers.counts.nonregular, boxers.counts.total) == (8, 1, 8)


def test_a_judges_day_total_covers_all_their_rings(parsed):
    pd = parsed["Twin-Brooks-2024-JP-Mon.pdf"]  # ring 3 at 12:45 is listed, with his total, before ring 6 at 8:30
    (check,) = [c for c in pd.report.checks if c[0] == "Mr. Alfred J Ferruggiaro total dogs"]
    assert check[1:] == (108, 108)


def test_a_day_whose_groups_are_in_the_breed_rings_is_excluded():
    (row,) = _manifest("0")
    assert row["file"].startswith("Brookhaven") and "H9/H10" in row["notes"]


def test_csv_round_trip(parsed, tmp_path):
    p0 = parsed["KC-Anne-Arundel-Carroll-KC-2024-JP-Thurs.pdf"].program
    write_program(p0, tmp_path)
    p1 = read_program(tmp_path)

    def sig(p):
        return sorted((sb.ring.number, sb.judge.name, sb.start, tuple((e.name, e.entries, e.counts) for e in sb.block.events))
                      for sb in p.blocks)

    assert sig(p0) == sig(p1)
    assert p1.show.closing_time == TimeOfDay.at(19)
    assert {(b.judge.name, b.start, b.length) for b in p0.breaks} == {(b.judge.name, b.start, b.length) for b in p1.breaks}
