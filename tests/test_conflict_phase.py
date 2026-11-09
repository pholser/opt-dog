"""S1 in the solver: a phase after structure, for exhibitors signed up for the accommodation."""

import pytest

from optdog.breeds import default_catalog
from optdog.conflicts import exhibitor_conflicts, registered_window_pairs
from optdog.domain import (
    VARIETY_GROUPS, Competition, Entry, EntryStatus, EventKind, Exhibitor, Judge, JudgingEvent, Ring, Show,
)
from optdog.params import ShowParameters
from optdog.solve.cpsat import solve_ordering
from optdog.validate import validate

CAT = default_catalog()
P = ShowParameters(overlap_tolerance_minutes=0)
A, B, C, G = Judge("Ms. A"), Judge("Mr. B"), Judge("Dr. C"), Judge("Dr. G")


def ev(breed, judge, n):
    return JudgingEvent(breed, EventKind.BREED, judge, n, CAT[breed], competition=Competition.ALL_BREED)


def show_of(events, judges, rings=3):
    return Show("T", "2026-01-01", list(judges), events, [Ring(i + 1) for i in range(rings)],
                group_judges={g: G for g in VARIETY_GROUPS}, bis_judge=G, group_rings=(Ring(1),), start=480)


def enter(show, exhibitor, *events, status=EntryStatus.CLASS):
    show.entries += [Entry(f"{exhibitor.id}-{i}", e, exhibitor, status) for i, e in enumerate(events)]


def minutes(res, show):
    res.program.show.entries = show.entries
    return exhibitor_conflicts(res.program, P.exhibitor_travel_minutes).minutes


def names(res):
    return [ph.name for ph in res.phases]


def test_conflict_moves_apart_when_the_finish_allows_it():
    pug, boxer = ev("Pug", A, 10), ev("Boxer", B, 10)
    show = show_of([pug, boxer, ev("Golden Retriever", C, 120)], (A, B, C))
    enter(show, Exhibitor("X", registered=True), pug, boxer)
    res = solve_ordering(show, P)
    assert "conflicts" in names(res)
    assert {v.code for v in validate(res.program, P).violations} == set()
    assert minutes(res, show) == 0  # also clears the travel margin between rings


def test_unavoidable_conflict_is_counted_as_the_scorer_counts_it():
    pug, boxer = ev("Pug", A, 25), ev("Boxer", B, 25)
    show = show_of([pug, boxer], (A, B))
    enter(show, Exhibitor("X", registered=True), pug, boxer)
    res = solve_ordering(show, P)
    # the finish comes first, and it needs the two breeds judged side by side
    assert minutes(res, show) == pytest.approx(res.costs["conflict_units"] / 10, abs=0.2)


def test_exhibitors_who_did_not_sign_up_are_ignored():
    pug, boxer = ev("Pug", A, 10), ev("Boxer", B, 10)
    show = show_of([pug, boxer, ev("Golden Retriever", C, 120)], (A, B, C))
    enter(show, Exhibitor("X", registered=False), pug, boxer)
    assert registered_window_pairs(show.entries) == []
    assert "conflicts" not in names(solve_ordering(show, P))


def test_a_judges_own_breeds_need_no_conflict_term():
    pug, maltese = ev("Pug", A, 10), ev("Maltese", A, 10)
    show = show_of([pug, maltese, ev("Golden Retriever", C, 50)], (A, C))
    enter(show, Exhibitor("X", registered=True), pug, maltese)
    assert "conflicts" not in names(solve_ordering(show, P))


def test_pairs_merge_an_exhibitors_dogs_and_count_exhibitors():
    pug, boxer = ev("Pug", A, 10), ev("Boxer", B, 10)
    entries = []
    for x in ("X", "Y"):
        e = Exhibitor(x, registered=True)
        entries += [Entry(f"{x}1", pug, e, EntryStatus.CLASS), Entry(f"{x}2", pug, e, EntryStatus.CHAMPION),
                    Entry(f"{x}3", boxer, e, EntryStatus.CHAMPION)]
    pairs = registered_window_pairs(entries)
    assert len(pairs) == 1
    a, b, oa, ob, n = pairs[0]
    assert (a, b, n) == (pug, boxer, 2)
    assert oa == (0.0, pug.duration)  # a class dog in the breed: busy for all of it
    assert ob == (boxer.bob_offset, boxer.duration)  # champions only: Best of Breed portion
