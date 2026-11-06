"""Saved schedules load back as the same schedule, on the show's own objects."""

from pathlib import Path

import pytest

from optdog.instance import instance_from_program
from optdog.io.program_csv import read_program
from optdog.solutions import load_result, save_result
from optdog.solve.cpsat import PlacementResult, PhaseStats
from optdog.solve.greedy import greedy_program
from optdog.validate import validate

BRADSHAW = Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw"


@pytest.mark.parametrize("day", ["2022-04-30-del-sur-kennel-club", "2026-09-12-sir-francis-drake-kennel-club"])
def test_saved_schedule_scores_the_same_when_loaded(day, tmp_path):
    show = instance_from_program(read_program(BRADSHAW / day))
    prog = greedy_program(show)
    res = PlacementResult(prog, 1000.0, 990.0, [PhaseStats("finish", "OPTIMAL", 1000.0, 990.0, 1.5)], {"blocks": 3.0})
    save_result(res, tmp_path / "ordering")
    back = load_result(tmp_path / "ordering", show)
    assert back.optimal and back.bound == 990.0 and back.costs == {"blocks": 3.0}
    a, b = validate(prog), validate(back.program)
    assert a.timeline.finish == pytest.approx(b.timeline.finish)
    assert a.soft == b.soft
    assert [v.code for v in a.violations] == [v.code for v in b.violations]
    assert {id(sb) for sb in show.pinned} <= {id(sb) for sb in back.program.blocks}
    assert sorted(id(e) for sb in back.program.blocks for e in sb.block.events) == sorted(id(e) for e in show.events)
