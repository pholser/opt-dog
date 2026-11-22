"""The MB-F parser, on the programs in data/raw/mbf."""

import csv
from pathlib import Path

import pytest

from optdog.domain import VARIETY_GROUPS, TimeOfDay
from optdog.ingest.mbf import MbfParser, recapitalize
from optdog.io.program_csv import read_program, write_program

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "mbf"

# Mismatches traced to the programs themselves: their figures disagree with each other.
KNOWN_SOURCE_ERRORS = {
    # the index and the listings both have 137 breeds and varieties
    ("jpg1134.pdf", "breeds or varieties"),
    # both judges' FSS open shows list 17 dogs, as the index and the cover's "34 dogs" in
    # concurrent events do; each judge's printed total is one more
    ("jpg328.pdf", "Mrs. Sandra Pretari Hickson total dogs"),
    ("jpg328.pdf", "Ms. Beverly R. Andersen total dogs"),
}


def _manifest():
    with open(RAW / "manifest.csv", newline="") as f:
        return [r for r in csv.DictReader(f) if r["include"] == "1"]


@pytest.fixture(scope="module")
def parsed():
    parser = MbfParser()
    return {r["file"]: parser.parse(RAW / r["file"])[0] for r in _manifest()}


def test_names_printed_in_a_lowercasing_font_are_recapitalized():
    assert recapitalize("dr. lawrence A. letsche, dVM") == "Dr. Lawrence A. Letsche, DVM"
    assert recapitalize("*Ms. Beverly r. Andersen") == "Ms. Beverly R. Andersen"
    assert recapitalize("Mrs. Sioux Forsyth-Green") == "Mrs. Sioux Forsyth-Green"


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: r["file"])
def test_the_programs_own_figures_agree_with_the_parse(parsed, row):
    pd = parsed[row["file"]]
    bad = [c for c in pd.report.failed_checks if (row["file"], c[0]) not in KNOWN_SOURCE_ERRORS]
    assert not bad, bad
    assert not pd.report.unknown_breeds and not pd.report.index_mismatches
    judges = {j.name for j in pd.program.show.judges}
    assert {f"{j} total dogs" for j in judges} <= {c[0] for c in pd.report.checks}
    assert {"breeds or varieties", "breed index rows (breeds) matching listings"} <= {c[0] for c in pd.report.checks}


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: r["file"])
def test_the_day_is_complete(parsed, row):
    prog = parsed[row["file"]].program
    plan = prog.groups
    assert plan is not None and plan.bis_judge is not None and plan.start is not None
    assert sorted(g.value for g in plan.order) == sorted(g.value for g in VARIETY_GROUPS)
    assert len(plan.rings) == 1
    assert all(j in prog.show.judges for j in prog.show.group_judges.values())
    assert all(e.breed is not None for e in prog.show.events)
    assert all(e.counts is None or e.counts.total == e.entries for e in prog.show.events)
    assert prog.show.closing_time is not None and prog.show.closing_time > plan.start
    assert prog.breaks


def test_the_entries_add_up_to_the_programs_total_entry(parsed):
    # the total entry is the breed entries plus Junior Showmanship
    for file, (breeds, juniors, total) in {"jpg1134.pdf": (687, 19, 706), "jpg328.pdf": (1032, 37, 1069)}.items():
        pd = parsed[file]
        assert sum(e.entries for e in pd.program.show.events) == breeds
        assert breeds + juniors == total


def test_what_is_not_breed_judging_is_left_out(parsed):
    show = parsed["jpg328.pdf"].program.show
    names = {e.name for e in show.events}
    assert "Tornjaks" not in names and "Presa Canario" not in names  # FSS open shows
    assert not any("Junior" in n for n in names)
    danes = [e for e in show.events if e.name == "Great Danes"]
    assert [e.entries for e in danes] == [36]  # a specialty held as the breed judging is kept; the 4-6 puppies are not
    assert any("4-6 Month Puppy" in x for x in parsed["jpg328.pdf"].report.not_kept)


def test_a_judges_day_total_includes_their_other_competitions(parsed):
    (check,) = [c for c in parsed["jpg1134.pdf"].report.checks if c[0] == "Mrs. Angela Pickett total dogs"]
    assert check[1:] == (143, 143)  # 105 breed entries in rings 5 and 1, and 38 4-6 Month puppies


def test_a_stint_continued_in_the_next_column_keeps_its_ring_and_judge(parsed):
    blocks = parsed["jpg1134.pdf"].program.blocks
    late = [sb for sb in blocks if sb.judge.name.startswith("Dr. Andrea") and sb.start == TimeOfDay.at(12, 30)]
    assert [(sb.ring.number, [e.name for e in sb.block.events]) for sb in late] == [
        (1, ["Chinese Cresteds", "Japanese Chin", "Afghan Hounds"])]


def test_csv_round_trip(parsed, tmp_path):
    p0 = parsed["jpg1134.pdf"].program
    write_program(p0, tmp_path)
    p1 = read_program(tmp_path)

    def sig(p):
        return sorted((sb.ring.number, sb.judge.name, sb.start, tuple((e.name, e.entries, e.counts) for e in sb.block.events))
                      for sb in p.blocks)

    assert sig(p0) == sig(p1)
    assert p1.show.closing_time == TimeOfDay.at(19)
