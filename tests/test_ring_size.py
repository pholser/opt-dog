"""Ring sizes: each breed's minimum ring (H16) and the group ring (H17)."""

from dataclasses import replace

import pytest

from optdog.breeds import BreedCatalog, default_catalog
from optdog.domain import (
    VARIETY_GROUPS, Block, Competition, EventKind, GroupsPlan, JudgingEvent, JudgingProgram, Judge, Ring,
    RingSize, ScheduledBlock, Show,
)
from optdog.io.program_csv import read_program, write_program
from optdog.params import ShowParameters
from optdog.solve.cpsat import solve_ordering, solve_packing
from optdog.solve.greedy import greedy_program
from optdog.validate import validate

CAT = default_catalog()
P0 = ShowParameters(overlap_tolerance_minutes=0)
A, B, G = Judge("Ms. A"), Judge("Mr. B"), Judge("Dr. G")


@pytest.mark.parametrize("breed, size", [
    ("Pug", "S"), ("Chihuahua (Smooth Coat)", "S"),
    ("Cairn Terrier", "M"), ("Beagle (13 Inch)", "M"), ("Basset Hound", "M"), ("Basenji", "M"), ("Bulldog", "M"),
    ("Pembroke Welsh Corgi", "M"), ("Shetland Sheepdog", "M"), ("English Springer Spaniel", "M"),
    ("Irish Wolfhound", "L"), ("Whippet", "L"), ("Airedale Terrier", "L"), ("Dalmatian", "L"),
    ("Poodle (Standard)", "L"), ("Miniature Bull Terrier", "L"), ("Labrador Retriever", "L"), ("Great Dane", "L"),
])
def test_minimum_ring_by_rule(breed, size):
    assert CAT[breed].min_ring is RingSize[size]


def test_the_catalog_can_override_the_rule(tmp_path):
    path = tmp_path / "breeds.csv"
    path.write_text("name,group,examination,variety_of,min_ring,aliases\n"
                    "Whippet,Hound,ground_or_ramp,,M,\nBorzoi,Hound,ground,,,\n")
    cat = BreedCatalog.load(path)
    assert cat["Whippet"].min_ring is RingSize.M and cat["Borzoi"].min_ring is RingSize.L


def test_events_and_blocks_need_their_largest_breed():
    pug = JudgingEvent("Pug", EventKind.BREED, A, 10, CAT["Pug"])
    dane = JudgingEvent("Great Dane", EventKind.BREED, A, 10, CAT["Great Dane"])
    juniors = JudgingEvent("Junior Showmanship", EventKind.JUNIOR_SHOWMANSHIP, A, 5)
    assert Block(A, (pug,)).min_ring is RingSize.S
    assert Block(A, (pug, dane)).min_ring is RingSize.L
    assert juniors.min_ring is RingSize.M
    assert Ring(3).size is RingSize.XL and Ring(3, RingSize.S) == Ring(3)


def _show(rings):
    toy = [JudgingEvent(b, EventKind.BREED, A, 15, CAT[b]) for b in ("Pug", "Maltese", "Papillon")]
    big = [JudgingEvent(b, EventKind.BREED, B, 15, CAT[b]) for b in ("Great Dane", "Mastiff", "Boxer")]
    return Show("T", "2026-01-01", [A, B], toy + big, rings, group_judges={g: G for g in VARIETY_GROUPS},
                bis_judge=G, group_rings=(rings[0],), start=480)


def test_validator_flags_a_breed_in_a_ring_too_small_and_a_small_group_ring():
    show = _show([Ring(1, RingSize.XL), Ring(2, RingSize.S), Ring(3, RingSize.L)])
    big = [e for e in show.events if e.judge == B]
    toy = [e for e in show.events if e.judge == A]
    plan = GroupsPlan((Ring(1, RingSize.XL),), 720, tuple(VARIETY_GROUPS), dict(show.group_judges), G)
    bad = JudgingProgram(show, [ScheduledBlock(Block(B, tuple(big)), show.rings[1], 480),
                                ScheduledBlock(Block(A, tuple(toy)), show.rings[2], 480)], [], plan)
    codes = [v.code for v in validate(bad, P0).violations]
    assert codes.count("H16") == 1
    small_group = replace(plan, rings=(Ring(1, RingSize.L),))
    assert "H17" in [v.code for v in validate(JudgingProgram(show, bad.blocks, [], small_group), P0).violations]


@pytest.mark.parametrize("solve", [lambda s: greedy_program(s, P0), lambda s: solve_ordering(s, P0).program,
                                   lambda s: solve_packing(s, P0).program], ids=["greedy", "3b", "3c"])
def test_schedulers_put_each_breed_in_a_ring_large_enough(solve):
    # the toy judge could use any ring; the giant breeds fit only ring 3 (ring 1 is for groups)
    show = _show([Ring(1, RingSize.XL), Ring(2, RingSize.S), Ring(3, RingSize.L)])
    prog = solve(show)
    assert prog is not None
    assert validate(prog, P0).violations == []
    assert {sb.ring.number for sb in prog.blocks if sb.judge == B} == {3}


def test_ring_sizes_survive_csv(tmp_path):
    show = _show([Ring(1, RingSize.XL), Ring(2, RingSize.S), Ring(3, RingSize.L)])
    prog = greedy_program(show, P0)
    write_program(prog, tmp_path)
    back = read_program(tmp_path)
    assert [(r.number, r.size) for r in back.show.rings] == [(1, RingSize.XL), (2, RingSize.S), (3, RingSize.L)]
    assert all(sb.ring.size is next(r.size for r in back.show.rings if r == sb.ring) for sb in back.blocks)
