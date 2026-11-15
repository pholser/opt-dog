"""Step 3c: breeds inside a run are ordered to pack into full blocks."""

from optdog.breeds import default_catalog
from optdog.domain import Duration, JudgingEvent, Judge
from optdog.solve.packing import items_of, pack_run

CAT = default_catalog()
A = Judge("Ms. A")


def ev(breed, n):
    return JudgingEvent(breed, A, n, CAT[breed])


def dogs(bins):
    return sorted(sum(e.entries for e in b) for b in bins)


def test_two_small_breeds_share_a_block_instead_of_sitting_beside_big_ones():
    run = [ev("Akita", 8), ev("Boxer", 26), ev("Mastiff", 8), ev("Rottweiler", 26)]
    bins = pack_run(run, Duration(65), 15)
    assert dogs(bins) == [16, 26, 26]
    assert sorted(e.name for b in bins for e in b) == sorted(e.name for e in run)


def test_a_breed_over_the_cap_has_a_block_to_itself():
    long, small = ev("Boxer", 37), ev("Akita", 4)
    assert dogs(pack_run([small, long], Duration(65), 15)) == [4, 37]


def test_no_bin_is_over_the_cap():
    run = [ev(b, 13) for b in ("Akita", "Boxer", "Mastiff", "Rottweiler", "Samoyed", "Komondor", "Kuvasz")]
    assert all(Duration.total(e.duration for e in b) <= Duration(65) for b in pack_run(run, Duration(65), 15))


def test_varieties_of_a_breed_stay_together():
    akita = ev("Akita", 5)
    poodles = [ev("Poodle (Miniature)", 3), ev("Poodle (Standard)", 3)]
    assert items_of([poodles[0], akita, poodles[1]]) == [[poodles[0], poodles[1]], [akita]]
