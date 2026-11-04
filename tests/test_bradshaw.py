import csv
from pathlib import Path

import pytest

from optdog.domain import VARIETY_GROUPS, EntryCounts, EventKind
from optdog.ingest.bradshaw import BradshawParser, _split_group_pairs, parse_counts, parse_time
from optdog.io.program_csv import read_program, write_program

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "bradshaw"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8:00 AM", 8 * 60),
        ("12 Noon", 12 * 60),
        ("12:00 NOON", 12 * 60),
        ("1:25 PM", 13 * 60 + 25),
        ("12:40 PM", 12 * 60 + 40),
        ("Lunch", None),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("4-2-(1-1)", EntryCounts(4, 2, 1, 1, 0)),
        ("0-1", EntryCounts(0, 1, 0, 0, 0)),
        ("16-25-4V-(12-8)", EntryCounts(16, 25, 12, 8, 4)),
        ("38-49-1VD-1VB-(13-12)", EntryCounts(38, 49, 13, 12, 2)),
        ("13-19-(12-12)-2", EntryCounts(13, 19, 12, 12, 2)),
        ("6-8-2-(5-7)-1 BB", EntryCounts(6, 8, 5, 7, 3)),
        ("0-0-1-V", EntryCounts(0, 0, 0, 0, 1)),
    ],
)
def test_parse_counts(text, expected):
    assert parse_counts(text) == expected


def test_split_group_pairs_variants():
    assert _split_group_pairs("Herding Mr. Murphy Herding Mr. Newton") == [
        ("herding", "Mr. Murphy"),
        ("herding", "Mr. Newton"),
    ]
    assert _split_group_pairs("SportingMr. Miller") == [("sporting", "Mr. Miller")]
    assert _split_group_pairs("Toys – Dr. Ioia") == [("toys", "Dr. Ioia")]
    assert _split_group_pairs("Best In Show Miss Lex") == [("BIS", "Miss Lex")]


# ---------------------------------------------------------------- dataset regression

# Mismatches traced to the source PDFs themselves (documented in manifest.csv).
KNOWN_SOURCE_ERRORS = {
    ("843.pdf", "SUNDAY", "ring 6 Mr. Larry C. Abbott subtotal"),
    ("843.pdf", "SUNDAY", "ring 12 Mr. Larry C. Abbott subtotal"),
}


def _manifest():
    with open(RAW / "manifest.csv", newline="") as f:
        return [r for r in csv.DictReader(f) if r["include"] == "1"]


@pytest.fixture(scope="module")
def parsed():
    parser = BradshawParser()
    out = {}
    for fname in sorted({r["file"] for r in _manifest()}):
        for pd in parser.parse(RAW / fname):
            out[(fname, pd.report.day)] = pd
    return out


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: f"{r['file']}-{r['day']}")
def test_every_breed_matches_program_index(parsed, row):
    pd = parsed[(row["file"], row["day"])]
    (idx,) = [c for c in pd.report.checks if c[0].startswith("breed index")]
    assert idx[1] == idx[2], pd.report.index_mismatches
    assert idx[1] > 100


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: f"{r['file']}-{r['day']}")
def test_ring_subtotals_match(parsed, row):
    pd = parsed[(row["file"], row["day"])]
    bad = [
        c
        for c in pd.report.failed_checks
        if "subtotal" in c[0] and (row["file"], row["day"], c[0]) not in KNOWN_SOURCE_ERRORS
    ]
    assert not bad


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: f"{r['file']}-{r['day']}")
def test_groups_plan_complete(parsed, row):
    plan = parsed[(row["file"], row["day"])].program.groups
    assert plan is not None
    assert sorted(g.value for g in plan.order) == sorted(g.value for g in VARIETY_GROUPS)
    assert plan.start is not None and 12 * 60 <= plan.start <= 16 * 60
    assert len(plan.rings) in (1, 2)
    assert plan.bis_judge is not None


@pytest.mark.parametrize("row", _manifest(), ids=lambda r: f"{r['file']}-{r['day']}")
def test_all_breeds_resolved(parsed, row):
    pd = parsed[(row["file"], row["day"])]
    unresolved = [e.name for e in pd.program.show.events if e.kind is EventKind.BREED and e.breed is None]
    assert not unresolved


def test_csv_round_trip(parsed, tmp_path):
    pd = parsed[("603.pdf", "SATURDAY")]
    write_program(pd.program, tmp_path)
    back = read_program(tmp_path)
    p0, p1 = pd.program, back

    def sig(p):
        return sorted(
            (sb.ring.number, sb.judge.name, sb.start, tuple((e.name, e.entries, e.counts) for e in sb.block.events))
            for sb in p.blocks
        )

    assert sig(p0) == sig(p1)
    assert p0.groups.order == p1.groups.order
    assert p0.groups.start == p1.groups.start
    assert [r.number for r in p0.groups.rings] == [r.number for r in p1.groups.rings]
    assert {(b.judge.name, b.start, b.minutes) for b in p0.breaks} == {(b.judge.name, b.start, b.minutes) for b in p1.breaks}
