"""Data sources: saved results load back as the same schedule, on the show's own objects, from
any backend."""

from pathlib import Path

import pytest

from optdog.domain import TimeOfDay
from optdog.instance import instance_from_program
from optdog.io.csv_dataset import CsvDataset
from optdog.solve.cpsat import PlacementResult, PhaseStats
from optdog.solve.greedy import greedy_program
from optdog.solve.relaxation import RelaxedBound
from optdog.sources import STEPS, MemoryDataset, ShowKey, published_day, result_on, solved_day
from optdog.validate import validate

REPO = CsvDataset()
DAYS = ["2022-04-30-del-sur-kennel-club", "2026-09-12-sir-francis-drake-kennel-club"]


def _result(prog):
    return PlacementResult(prog, TimeOfDay(1000.0), TimeOfDay(990.0),
                           [PhaseStats("finish", "OPTIMAL", 1000.0, 990.0, 1.5)], {"blocks": 3.0})


@pytest.fixture(params=["csv", "memory"])
def store(request, tmp_path):
    return CsvDataset(tmp_path) if request.param == "csv" else MemoryDataset()


@pytest.mark.parametrize("day", DAYS)
def test_saved_schedule_scores_the_same_when_loaded(day, store):
    key = ShowKey("bradshaw", day)
    show = instance_from_program(REPO.program(key))
    prog = greedy_program(show)
    store.put_result(key, "ordering", _result(prog))
    back = result_on(store.result(key, "ordering"), show)
    assert back.optimal and back.bound == TimeOfDay(990.0) and back.costs == {"blocks": 3.0}
    a, b = validate(prog), validate(back.program)
    assert a.timeline.finish.minutes == pytest.approx(b.timeline.finish.minutes)
    assert a.soft == b.soft
    assert [v.code for v in a.violations] == [v.code for v in b.violations]
    assert {id(sb) for sb in show.pinned} <= {id(sb) for sb in back.program.blocks}
    assert sorted(id(e) for sb in back.program.blocks for e in sb.block.events) == sorted(id(e) for e in show.events)


def test_a_day_assembles_the_same_from_any_backend(store):
    key = ShowKey("bradshaw", DAYS[1])
    human = REPO.program(key)
    store.put_program(key, human, origin="test")
    entries = REPO.entries(key, human.show.events)
    assert entries is not None
    store.put_entries(key, entries)
    assert store.program_keys() == [key]
    assert store.entries(key, human.show.events, seed=1) is None

    got, show = published_day(store, store, key)
    want, _ = published_day(REPO, REPO, key)
    assert validate(got).soft == validate(want).soft  # S1 included: the exhibitors came along

    with pytest.raises(LookupError):
        solved_day(store, store, store, key)
    store.put_bound(key, RelaxedBound(TimeOfDay(990.0), TimeOfDay(990.0), TimeOfDay(980.0), None, "OPTIMAL", 1.0))
    for step in STEPS:
        store.put_result(key, step, _result(greedy_program(show)))
    d = solved_day(store, store, store, key)
    assert d.name == DAYS[1] and d.bound.value == TimeOfDay(990.0) and set(d.results) == set(STEPS)
