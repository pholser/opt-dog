"""CP-SAT placement (step 1): valid, never worse than the greedy, never better than the bound."""

from dataclasses import replace
from pathlib import Path

import pytest

from optdog.breeds import default_catalog
from optdog.domain import (
    VARIETY_GROUPS, Block, Competition, EventKind, GroupsPlan, JudgingEvent, JudgingProgram, Judge, Ring,
    ScheduledBlock, Show,
)
from optdog.instance import instance_from_program
from optdog.io.program_csv import read_program
from optdog.params import ShowParameters
from optdog.solve.bounds import finish_lower_bound
from optdog.solve.relaxation import relaxed_lower_bound
from optdog.solve.cpsat import PlacementOptions, solve_formation, solve_ordering, solve_placement
from optdog.solve.greedy import greedy_program
from optdog.validate import validate

CAT = default_catalog()
P0 = ShowParameters(overlap_tolerance_minutes=0)
FAST = PlacementOptions(time_limit=20, tidy=False)
PARSED = sorted((Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw").iterdir())
A, B, C, G = Judge("Ms. A"), Judge("Mr. B"), Judge("Dr. C"), Judge("Dr. G")


def ev(breed, judge, n, competition=Competition.ALL_BREED):
    return JudgingEvent(breed, EventKind.BREED, judge, n, CAT[breed], competition=competition)


def show_of(events, judges, rings=3):
    return Show("T", "2026-01-01", list(judges), events, [Ring(i + 1) for i in range(rings)],
                group_judges={g: G for g in VARIETY_GROUPS}, bis_judge=G, group_rings=(Ring(1),), start=480)


def test_small_show_is_valid_and_matches_the_validator():
    show = show_of([ev("Pug", A, 25), ev("Boxer", A, 25), ev("Beagle (13 Inch)", B, 25), ev("Maltese", B, 10),
                    ev("Golden Retriever", C, 20)], (A, B, C))
    res = solve_placement(show, P0)
    assert res.optimal
    v = validate(res.program, P0)
    assert v.violations == []
    assert v.timeline.finish == pytest.approx(res.finish, abs=0.1)
    assert res.finish >= finish_lower_bound(show, P0).value - 1e-6


def test_back_to_back_pinned_blocks_for_one_judge():
    # two pinned blocks 4 minutes apart: padding each by the travel margin must not make them clash
    p1, p2 = ev("Pug", C, 5, Competition.BEGINNER_PUPPY), ev("Maltese", C, 5, Competition.BEGINNER_PUPPY)
    pins = [ScheduledBlock(Block(C, (p1,)), Ring(3), 13 * 60), ScheduledBlock(Block(C, (p2,)), Ring(3), 13 * 60 + 16)]
    show = show_of([ev("Boxer", A, 25), ev("Beagle (13 Inch)", B, 25), p1, p2], (A, B, C))
    show.pinned = pins
    res = solve_placement(show, P0)
    assert res.program is not None
    assert validate(res.program, P0).violations == []
    assert all(sb in res.program.blocks for sb in pins)


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_placement_on_parsed_shows(d):
    show = instance_from_program(read_program(d))
    res = solve_placement(show, options=FAST)
    assert res.program is not None, res.phases
    v = validate(res.program)
    assert {x.code for x in v.violations} <= {"V1"}, [str(x) for x in v.violations]
    assert v.timeline.finish == pytest.approx(res.finish, abs=0.1)
    assert res.finish <= validate(greedy_program(show)).timeline.finish + 1e-6
    assert res.finish >= finish_lower_bound(show).value - 1e-6


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_formation_on_parsed_shows(d):
    """Step 3a: choosing the block breaks never does worse than placing the greedy's blocks."""
    show = instance_from_program(read_program(d))
    placed = solve_placement(show, options=FAST)
    res = solve_formation(show, options=FAST)
    assert res.program is not None, res.phases
    v = validate(res.program)
    assert {x.code for x in v.violations} <= {"V1"}, [str(x) for x in v.violations]
    assert v.timeline.finish == pytest.approx(res.finish, abs=0.1)
    assert res.finish <= placed.finish + 1e-6
    assert res.finish >= relaxed_lower_bound(show, time_limit=20).value - 1e-6


def test_formation_keeps_sweepstakes_with_their_breed_and_caps_blocks():
    sw = JudgingEvent("Pug Sweepstakes", EventKind.SWEEPSTAKES, A, 6, CAT["Pug"])
    events = [ev("Boxer", A, 20), sw, ev("Pug", A, 20), ev("Maltese", A, 20), ev("Beagle (13 Inch)", B, 25)]
    res = solve_formation(show_of(events, (A, B)), P0)
    assert res.program is not None
    for sb in res.program.blocks:
        assert sb.block.duration <= 65 + 1e-6
        names = [e.name for e in sb.block.events]
        if "Pug Sweepstakes" in names:
            assert names[names.index("Pug Sweepstakes") + 1] == "Pug"
    assert validate(res.program, P0).violations == []


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_ordering_on_parsed_shows(d):
    """Step 3b: choosing each judge's group order never does worse than the greedy's order."""
    show = instance_from_program(read_program(d))
    placed = solve_placement(show, options=FAST)
    res = solve_ordering(show, options=FAST)
    assert res.program is not None, res.phases
    v = validate(res.program)
    assert {x.code for x in v.violations} <= {"V1"}, [str(x) for x in v.violations]
    assert v.timeline.finish == pytest.approx(res.finish, abs=0.1)
    assert res.finish <= placed.finish + 1e-6
    assert res.finish >= relaxed_lower_bound(show, time_limit=20).value - 1e-6


def test_ordering_keeps_groups_together_and_meets_table_runs():
    # greedy order: Golden (ground), ECS (table) | Greyhound (ground), Beagle (table): 3 switches.
    # Flipping one group's equipment order makes like meet like at the group boundary: 2.
    events = [ev("Golden Retriever", A, 10), ev("English Cocker Spaniel", A, 10),
              ev("Greyhound", A, 10), ev("Beagle (13 Inch)", A, 10), ev("Boxer", B, 25)]
    p = replace(P0, equipment_free_gap_minutes=10_000)  # no idle time makes a switch free
    res = solve_ordering(show_of(events, (A, B)), p)
    assert res.program is not None
    v = validate(res.program, p)
    assert v.violations == []
    order = [e for sb in sorted(res.program.blocks, key=lambda b: b.start) if sb.judge == A for e in sb.block.events]
    groups = [e.group for e in order]
    assert groups[:2] == [groups[0]] * 2 and groups[2:] == [groups[2]] * 2  # each group contiguous
    kinds = [e.equipment for e in order]
    assert kinds[1] == kinds[2]
    assert v.soft["S3_equipment_switches"] == 2


def test_ordering_leaves_no_idle_time_in_a_judges_day_when_nothing_forces_it():
    # 75 dogs take three hours: more than one block, with nothing to wait for between them
    events = [ev("Boxer", A, 25), ev("Rottweiler", A, 25), ev("Great Dane", A, 25), ev("Pug", B, 10)]
    res = solve_ordering(show_of(events, (A, B)), P0)
    assert res.program is not None
    seq = res.program.judge_schedule(A)
    assert len(seq) >= 2
    assert sum(b.start - a.end for a, b in zip(seq, seq[1:])) < 5  # the grid may round a start up


def test_obedience_is_a_fixed_commitment_for_a_judge_who_also_judges_conformation():
    d = Judge("Mrs. D")  # obedience only: not part of the problem
    obed = JudgingEvent("Obedience-Novice A", EventKind.OTHER, A, 20, competition=Competition.OBEDIENCE)
    only = JudgingEvent("Obedience-Open A", EventKind.OTHER, d, 20, competition=Competition.OBEDIENCE)
    boxer, rott, pug = ev("Boxer", A, 25), ev("Rottweiler", A, 25), ev("Pug", B, 10)
    blocks = [ScheduledBlock(Block(A, (boxer,)), Ring(2), 480), ScheduledBlock(Block(A, (rott,)), Ring(2), 660),
              ScheduledBlock(Block(B, (pug,)), Ring(3), 480),
              ScheduledBlock(Block(A, (obed,)), Ring(9), 545), ScheduledBlock(Block(d, (only,)), Ring(8), 480)]
    base = show_of([boxer, rott, pug, obed, only], (A, B, d))
    plan = GroupsPlan((Ring(1),), 780, tuple(VARIETY_GROUPS), dict(base.group_judges), G)
    show = instance_from_program(JudgingProgram(base, blocks, [], plan))
    assert d not in show.judges and only not in show.events
    assert Ring(9) not in show.rings and any(sb.block.events == (obed,) for sb in show.pinned)
    res = solve_ordering(show, P0)
    assert res.program is not None
    assert validate(res.program, P0).violations == []
    assert not any(sb.ring == Ring(9) for sb in res.program.blocks if sb.block.events != (obed,))
    seq = res.program.judge_schedule(A)
    assert any(sb.block.events == (obed,) for sb in seq)
    assert all(a.end <= b.start for a, b in zip(seq, seq[1:]))
    # the commitment holds the judge, not one of the conformation rings
    assert relaxed_lower_bound(show, P0).value <= res.finish + 1e-6
