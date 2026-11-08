"""Step 3c: breeds inside a run are ordered to pack into full blocks."""

from optdog.breeds import default_catalog
from optdog.domain import Competition, EventKind, JudgingEvent, Judge
from optdog.solve.packing import items_of, pack_run

CAT = default_catalog()
A = Judge("Ms. A")


def ev(breed, n, kind=EventKind.BREED):
    return JudgingEvent(breed, kind, A, n, CAT[breed], competition=Competition.ALL_BREED)


def dogs(bins):
    return sorted(sum(e.entries for e in b) for b in bins)


def test_two_small_breeds_share_a_block_instead_of_sitting_beside_big_ones():
    run = [ev("Akita", 8), ev("Boxer", 26), ev("Mastiff", 8), ev("Rottweiler", 26)]
    bins = pack_run(run, 65, 15)
    assert dogs(bins) == [16, 26, 26]
    assert sorted(e.name for b in bins for e in b) == sorted(e.name for e in run)


def test_a_breed_over_the_cap_has_a_block_to_itself():
    long, small = ev("Boxer", 37), ev("Akita", 4)
    assert dogs(pack_run([small, long], 65, 15)) == [4, 37]


def test_no_bin_is_over_the_cap():
    run = [ev(b, 13) for b in ("Akita", "Boxer", "Mastiff", "Rottweiler", "Samoyed", "Komondor", "Kuvasz")]
    assert all(sum(e.duration for e in b) <= 65 for b in pack_run(run, 65, 15))


def test_a_sweepstakes_stays_just_before_its_breed_and_varieties_stay_together():
    sw = JudgingEvent("Boxer Sweepstakes", EventKind.SWEEPSTAKES, A, 3, CAT["Boxer"], competition=Competition.ALL_BREED)
    boxer, akita = ev("Boxer", 20), ev("Akita", 5)
    assert items_of([akita, sw, boxer]) == [[akita], [sw, boxer]]
    poodles = [ev("Poodle (Miniature)", 3), ev("Poodle (Standard)", 3)]
    assert len(items_of([poodles[0], akita, poodles[1]])) == 2
    flat = [e for b in pack_run([akita, sw, boxer], 65, 15) for e in b]
    assert flat.index(boxer) == flat.index(sw) + 1
