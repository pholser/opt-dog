from optdog.breeds import default_catalog
from optdog.domain import EventKind, Group, JudgingEvent, Judge, Show


def _event(name, judge, entries=3, kind=EventKind.BREED):
    return JudgingEvent(name, kind, judge, entries, default_catalog()[name])


def test_miscellaneous_breeds_do_not_feed_a_group():
    j = Judge("Ms. A")
    pio = _event("Peruvian Inca Orchid", j)
    golden = _event("Golden Retriever", j)
    assert pio.group is Group.MISCELLANEOUS
    assert pio.feeds_group is None
    assert golden.feeds_group is Group.SPORTING

    show = Show("Test KC", "2026-01-01", [j], [pio, golden], [])
    assert show.breeds_in(Group.MISCELLANEOUS) == [pio]
    assert show.feeding(Group.MISCELLANEOUS) == []
    assert show.feeding(Group.SPORTING) == [golden]


def test_sweepstakes_do_not_feed_a_group():
    j = Judge("Ms. A")
    sweeps = _event("Golden Retriever", j, kind=EventKind.SWEEPSTAKES)
    assert sweeps.group is None
    assert sweeps.feeds_group is None
