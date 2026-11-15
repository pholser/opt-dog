"""TimeOfDay and Duration: arithmetic between them, and no mixing with plain numbers."""

import pytest

from optdog.domain import Duration, TimeOfDay


def test_time_and_duration_arithmetic():
    t = TimeOfDay.at(8, 30)
    assert t + Duration(45) == TimeOfDay.at(9, 15)
    assert Duration(45) + t == TimeOfDay.at(9, 15)
    assert t - Duration(30) == TimeOfDay.at(8)
    assert TimeOfDay.at(9) - t == Duration(30)
    assert Duration(2.4) * 25 == 25 * Duration(2.4)
    assert Duration(60) / 4 == Duration(15) and Duration(60) / Duration(15) == 4
    assert Duration.total([Duration(1), Duration(2)]) == Duration(3)
    assert Duration.total([Duration(2.4)] * 25) == Duration(60)  # correctly rounded, unlike 2.4 + 2.4 + ...
    assert not Duration.ZERO and Duration(1)


def test_plain_numbers_do_not_mix():
    t, d = TimeOfDay.at(8), Duration(5)
    for bad in (lambda: t + 5, lambda: t - 5, lambda: t < 480, lambda: t == 480, lambda: d + 5, lambda: d <= 5,
                lambda: d == 5, lambda: t + t, lambda: d - t, lambda: sum([d, d])):
        with pytest.raises(TypeError):
            bad()


def test_formats_round_to_the_minute():
    assert str(TimeOfDay(510)) == "8:30 AM" and str(TimeOfDay(12 * 60)) == "12:00 PM"
    assert str(TimeOfDay(0)) == "12:00 AM" and str(TimeOfDay(13 * 60 + 4.6)) == "1:05 PM"
    assert TimeOfDay(510).hhmm() == "08:30" and TimeOfDay.parse(" 13:05") == TimeOfDay.at(13, 5)
    assert str(Duration(45)) == "45 min" and str(Duration(2.4)) == "2.4 min"


def test_grid_and_solver_units():
    assert TimeOfDay(507).ceil(Duration(5)) == TimeOfDay(510) and TimeOfDay(510).ceil(Duration(5)) == TimeOfDay(510)
    assert TimeOfDay(510).on_grid(Duration(5)) and not TimeOfDay(512).on_grid(Duration(5))
    assert TimeOfDay(510.4).units(10) == 5104 and TimeOfDay.from_units(5104, 10) == TimeOfDay(510.4)
    assert Duration(2.4).units(10) == 24 and Duration.from_units(24, 10) == Duration(2.4)


def test_equal_values_hash_alike():
    assert {TimeOfDay(510): "a"}[TimeOfDay(510.0)] == "a"
    assert len({Duration(5), Duration(5.0)}) == 1
