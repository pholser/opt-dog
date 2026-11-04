"""Lower bounds, the greedy baseline, and H13, on hand-built shows and on the parsed programs."""

from pathlib import Path

import pytest

from optdog.breeds import default_catalog
from optdog.domain import (
    Block, EventKind, Group, GroupsPlan, JudgingEvent, JudgingProgram, Judge, Ring, ScheduledBlock, Show,
    Competition, VARIETY_GROUPS,
)
from optdog.instance import instance_from_program
from optdog.io.program_csv import read_program
from optdog.params import ShowParameters
from optdog.solve.bounds import finish_lower_bound
from optdog.solve.greedy import greedy_program, pack_blocks, running_order
from optdog.validate import validate

CAT = default_catalog()
P0 = ShowParameters(overlap_tolerance_minutes=0)
PARSED = sorted((Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw").iterdir())

A, B, G = Judge("Ms. A"), Judge("Mr. B"), Judge("Dr. G")


def ev(breed, judge, n, kind=EventKind.BREED, competition=Competition.ALL_BREED):
    return JudgingEvent(breed, kind, judge, n, CAT[breed], competition=competition)


def small_show(events, rings=2, judges=(A, B)):
    return Show("T", "2026-01-01", list(judges), events, [Ring(i + 1) for i in range(rings)],
                group_judges={g: G for g in VARIETY_GROUPS}, bis_judge=G, group_rings=(Ring(1),), start=480)


def test_chain_bound_by_hand():
    # A: Pugs (Toy) then Boxers (Working), an hour each; B: Beagles (Hound), an hour.
    show = small_show([ev("Pug", A, 25), ev("Boxer", A, 25), ev("Beagle (13 Inch)", B, 25)])
    b = finish_lower_bound(show)
    # H13: two groups done first. Hound plus either of A's groups can be done by 9:00,
    # but A needs two hours for both Toy and Working, so the third entered group waits for 10:00.
    entered = [g for g in b.order if show.group_entrants(g) > 0]
    assert min(b.group_starts.values()) == 540
    assert sorted(b.group_starts[g] for g in entered)[-1] == 600
    # the last entered group ends 10:02.4, then BIS with 3 groups entered at 2.4 min each
    assert b.chain == pytest.approx(600 + 2.4 + 3 * 2.4)


def test_h13_two_groups_needed_or_one_if_only_one_entered():
    show = small_show([ev("Pug", A, 25), ev("Beagle (13 Inch)", B, 50)])  # Toy done 9:00, Hound 10:00
    blocks = [ScheduledBlock(Block(A, (show.events[0],)), Ring(2), 480),
              ScheduledBlock(Block(B, (show.events[1],)), Ring(3), 480)]
    plan = GroupsPlan((Ring(1),), 9 * 60 + 30, (Group.TOY, Group.HOUND), show.group_judges, G)
    prog = JudgingProgram(show, blocks, [], plan)
    assert "H13" in {v.code for v in validate(prog, P0).violations}
    assert "H13" not in {v.code for v in validate(prog, ShowParameters(overlap_tolerance_minutes=0,
                                                                         groups_complete_before_start=1)).violations}


def test_running_order_follows_target_groups_and_keeps_tables_together():
    evs = [ev("Cocker Spaniel (Black)", A, 3), ev("Boxer", A, 3), ev("Golden Retriever", A, 3),
           ev("English Cocker Spaniel", A, 3), ev("Brittany", A, 3), ev("Dutch Shepherd", A, 2)]
    rank = {g: i for i, g in enumerate([Group.WORKING, Group.SPORTING] + [g for g in VARIETY_GROUPS
                                                                            if g not in (Group.WORKING, Group.SPORTING)])}
    order = [e.name for e in running_order(evs, rank)]
    assert order[0] == "Boxer" and order[-1] == "Dutch Shepherd"  # target group first; Miscellaneous last
    sporting = [e for e in running_order(evs, rank) if e.group is Group.SPORTING]
    kinds = ["table" if e.breed.examination.needs_table else "other" for e in sporting]
    assert kinds == sorted(kinds, key=lambda k: k == "table")  # table breeds together, after ground


def test_pack_blocks_about_an_hour_and_big_breeds_alone():
    evs = [ev("Pug", A, 12), ev("Maltese", A, 12), ev("Papillon", A, 5), ev("Golden Retriever", A, 40), ev("Boxer", A, 3)]
    blocks = pack_blocks(A, evs, 65)
    # 28.8 + 28.8 = 57.6 min; Papillons would make 69.6 > 65, so they start the next block
    assert [len(b.events) for b in blocks] == [2, 1, 1, 1]
    assert all(b.duration <= 65 or len(b.events) == 1 for b in blocks)
    assert any(b.events == (evs[3],) for b in blocks)


def test_greedy_small_show_is_valid_and_not_below_bound():
    show = small_show([ev("Pug", A, 25), ev("Boxer", A, 25), ev("Beagle (13 Inch)", B, 25), ev("Maltese", B, 10)], rings=3)
    prog = greedy_program(show, P0)
    res = validate(prog, P0)
    assert res.violations == []
    assert res.timeline.finish >= finish_lower_bound(show, P0).value - 1e-6
    scheduled = [id(e) for sb in prog.blocks for e in sb.block.events]
    assert sorted(scheduled) == sorted(id(e) for e in show.events)


def test_greedy_respects_pinned_blocks():
    spec = ev("Pug", A, 10, competition=Competition.SPECIALTY)
    pinned = ScheduledBlock(Block(A, (spec,)), Ring(2), 9 * 60)
    show = small_show([spec, ev("Maltese", A, 25), ev("Boxer", B, 25)])
    show.pinned = [pinned]
    prog = greedy_program(show, P0)
    assert pinned in prog.blocks
    assert validate(prog, P0).violations == []


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_greedy_on_parsed_shows(d):
    human = read_program(d)
    show = instance_from_program(human)
    prog = greedy_program(show)
    res = validate(prog)
    assert {v.code for v in res.violations} <= {"V1"}  # V1 is an input overload, as in the human program
    assert res.timeline.finish >= finish_lower_bound(show).value - 1e-6
    # every conformation event exactly once; pinned blocks untouched
    scheduled = sorted(id(e) for sb in prog.blocks for e in sb.block.events)
    assert scheduled == sorted(id(e) for e in show.events)
    assert all(sb in prog.blocks for sb in show.pinned)
    # the bound never exceeds what the human program achieves either
    assert validate(human).timeline.finish >= finish_lower_bound(show).value - 1e-6


def test_h14_groups_wait_for_every_judges_lunch():
    from optdog.domain import Break

    show = small_show([ev("Pug", A, 25), ev("Beagle (13 Inch)", B, 25), ev("Dutch Shepherd", B, 5)])
    blocks = [ScheduledBlock(Block(A, (show.events[0],)), Ring(2), 480),
              ScheduledBlock(Block(B, (show.events[1],)), Ring(3), 480),
              ScheduledBlock(Block(B, (show.events[2],)), Ring(3), 12 * 60 + 30)]  # Miscellaneous, after lunch
    plan = GroupsPlan((Ring(1),), 12 * 60, (Group.TOY, Group.HOUND), show.group_judges, G)
    lunch = Break(B, Ring(3), 11 * 60 + 45, 45)  # B is at lunch until 12:30, after groups start
    prog = JudgingProgram(show, blocks, [lunch], plan)
    assert "H14" in {v.code for v in validate(prog, P0).violations}
    off = ShowParameters(overlap_tolerance_minutes=0, groups_after_all_lunches=False)
    assert "H14" not in {v.code for v in validate(prog, off).violations}
    # a break after the judge's last block is free time, not lunch: no H14
    trailing = JudgingProgram(show, blocks[:2], [lunch], plan)
    assert "H14" not in {v.code for v in validate(trailing, P0).violations}


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_h14_holds_in_every_human_program(d):
    assert "H14" not in {v.code for v in validate(read_program(d)).violations}


def test_h6_lunch_must_start_in_window():
    from optdog.domain import Break

    blocks = [ScheduledBlock(Block(A, (ev(b, A, 25),)), Ring(1), t) for b, t in
              [("Pug", 480), ("Maltese", 540), ("Papillon", 600), ("Havanese", 705), ("Shih Tzu", 765), ("Pekingese", 825)]]
    show = small_show([e for sb in blocks for e in sb.block.events], judges=(A,))
    early = JudgingProgram(show, blocks, [Break(A, Ring(1), 660, 45)], None)  # 11:00 lunch: in the default window
    assert "H6" not in {v.code for v in validate(early, P0).violations}
    narrow = ShowParameters(overlap_tolerance_minutes=0, lunch_window=(11 * 60 + 30, 12 * 60 + 30))
    assert "H6" in {v.code for v in validate(early, narrow).violations}
    # without a published break, the 11:00–11:45 gap counts only if a lunch can start in the window
    gap_only = JudgingProgram(show, blocks, [], None)
    assert "H6" not in {v.code for v in validate(gap_only, P0).violations}
    assert "H6" in {v.code for v in validate(gap_only, narrow).violations}


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_human_lunches_start_in_default_window(d):
    assert not [v for v in validate(read_program(d)).violations if v.code == "H6"]


def test_show_lunch_window_is_an_input_that_overrides_the_default(tmp_path):
    from dataclasses import replace

    from optdog.io.program_csv import write_program

    show = small_show([ev(b, A, 25) for b in ["Pug", "Maltese", "Papillon", "Havanese", "Shih Tzu", "Pekingese"]],
                      judges=(A,))
    late = replace(show, lunch_window=(12 * 60 + 30, 13 * 60))  # concessions open late at this venue
    prog = greedy_program(late, P0)
    assert prog.breaks and all(12 * 60 + 30 <= br.start <= 13 * 60 for br in prog.breaks)
    assert "H6" not in {v.code for v in validate(prog, P0).violations}
    assert "H6" in {v.code for v in validate(replace(prog, show=replace(late, lunch_window=(11 * 60, 11 * 60 + 30))),
                                             P0).violations}
    write_program(prog, tmp_path)
    assert read_program(tmp_path).show.lunch_window == (12 * 60 + 30, 13 * 60)
