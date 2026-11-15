from optdog.breeds import default_catalog
from optdog.domain import Duration, Group, JudgingEvent, Judge, Show, TimeOfDay

EIGHT = TimeOfDay.at(8)


def _event(name, judge, entries=3):
    return JudgingEvent(name, judge, entries, default_catalog()[name])


def test_miscellaneous_breeds_do_not_feed_a_group():
    j = Judge("Ms. A")
    pio = _event("Peruvian Inca Orchid", j)
    golden = _event("Golden Retriever", j)
    assert pio.group is Group.MISCELLANEOUS
    assert pio.feeds_group is None
    assert golden.feeds_group is Group.SPORTING

    show = Show("Test KC", "2026-01-01", [j], [pio, golden], [], start=EIGHT)
    assert show.breeds_in(Group.MISCELLANEOUS) == [pio]
    assert show.feeding(Group.MISCELLANEOUS) == []
    assert show.feeding(Group.SPORTING) == [golden]


def test_group_and_bis_durations():
    breed_judge = Judge("Ms. A")
    sporting_judge = Judge("Mr. Permit", permit=True)  # 3.0 min/dog
    hound_judge = Judge("Mrs. Regular")  # 2.4 min/dog
    bis_judge = Judge("Dr. BIS")
    events = [
        _event("Golden Retriever", breed_judge, 40),
        _event("Cocker Spaniel (Black)", breed_judge, 5),  # varieties each send a BOV winner
        _event("Cocker Spaniel (ASCOB)", breed_judge, 3),
        _event("Cocker Spaniel (Parti-Color)", breed_judge, 2),
        _event("Whippet", breed_judge, 20),
        _event("Peruvian Inca Orchid", breed_judge, 2),  # Miscellaneous: no group
    ]
    show = Show("Test KC", "2026-01-01", [breed_judge], events, [], start=EIGHT,
                group_judges={Group.SPORTING: sporting_judge, Group.HOUND: hound_judge},
                bis_judge=bis_judge)

    assert show.group_entrants(Group.SPORTING) == 4
    assert show.group_duration(Group.SPORTING) == Duration(4 * 3.0)
    assert show.group_entrants(Group.HOUND) == 1
    assert show.group_duration(Group.HOUND) == Duration(1 * 2.4)
    assert show.group_entrants(Group.TOY) == 0
    assert show.group_duration(Group.TOY) == Duration.ZERO
    assert show.bis_entrants() == 2  # only Sporting and Hound have breeds entered
    assert show.bis_duration() == Duration(2 * 2.4)
    assert show.groups_and_bis_duration() == Duration(12.0 + 2.4 + 4.8)
