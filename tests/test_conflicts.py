from collections import Counter
from pathlib import Path

from optdog.breeds import default_catalog
from optdog.conflicts import exhibitor_conflicts
from optdog.domain import (
    Block, EntryCounts, Entry, EntryStatus, EventKind, Exhibitor, JudgingEvent, JudgingProgram, Judge, Ring,
    ScheduledBlock, Show,
)
from optdog.io.program_csv import read_entries, read_program, write_entries
from optdog.synth.exhibitors import ExhibitorModel, generate

CAT = default_catalog()
ROOT = Path(__file__).resolve().parents[1]
A, B = Judge("Ms. A"), Judge("Mr. B")


def ev(breed, judge, counts):
    return JudgingEvent(breed, EventKind.BREED, judge, counts.total, CAT[breed], counts)


def prog(placements, entries):
    """placements: [(event, ring, start)] — one block per event."""
    blocks = [ScheduledBlock(Block(e.judge, (e,)), Ring(r), t) for e, r, t in placements]
    show = Show("T", "2026-01-01", [A, B], [e for e, _, _ in placements], [Ring(r) for _, r, _ in placements],
                entries=entries)
    return JudgingProgram(show, blocks)


def test_class_dog_busy_whole_breed_champion_only_bob():
    pugs = ev("Pug", A, EntryCounts(5, 5, 3, 2, 0))  # 15 dogs = 36 min; BOB after 10 dogs = 24 min
    x = Exhibitor("X", registered=True)
    assert Entry("d1", pugs, x, EntryStatus.CLASS).busy_offset == (0.0, 36.0)
    assert Entry("d2", pugs, x, EntryStatus.CHAMPION).busy_offset == (24.0, 36.0)


def test_overlap_minutes_between_rings():
    x = Exhibitor("X", registered=True)
    pugs = ev("Pug", A, EntryCounts(10, 10, 3, 2, 0))  # 25 dogs, 60 min
    boxers = ev("Boxer", B, EntryCounts(5, 5, 0, 0, 0))  # 10 dogs, 24 min
    entries = [Entry("d1", pugs, x, EntryStatus.CLASS), Entry("d2", boxers, x, EntryStatus.CLASS)]
    # Pugs ring 1 8:00–9:00; Boxers ring 2 8:30–8:54 → 24 min overlap (+ margin clipped by Boxer window)
    p = prog([(pugs, 1, 480), (boxers, 2, 510)], entries)
    assert exhibitor_conflicts(p, travel_minutes=0).minutes == 24
    # back to back in different rings: Boxers start 9:01 → 1 min short of a 2-min walk
    p = prog([(pugs, 1, 480), (boxers, 2, 541)], entries)
    assert exhibitor_conflicts(p, travel_minutes=0).minutes == 0
    assert exhibitor_conflicts(p, travel_minutes=2).minutes == 1


def test_champion_only_conflicts_with_bob_portion():
    x = Exhibitor("X", registered=True)
    pugs = ev("Pug", A, EntryCounts(10, 10, 3, 2, 0))  # BOB from 8:48 to 9:00
    boxers = ev("Boxer", B, EntryCounts(5, 5, 0, 0, 0))
    entries = [Entry("d1", pugs, x, EntryStatus.CHAMPION), Entry("d2", boxers, x, EntryStatus.CLASS)]
    p = prog([(pugs, 1, 480), (boxers, 2, 480)], entries)  # Boxers 8:00–8:24: before Pug BOB
    assert exhibitor_conflicts(p, travel_minutes=2).minutes == 0


def test_same_ring_unregistered_and_same_breed_do_not_count():
    x = Exhibitor("X", registered=True)
    y = Exhibitor("Y", registered=False)
    pugs = ev("Pug", A, EntryCounts(10, 10, 3, 2, 0))
    boxers = ev("Boxer", A, EntryCounts(5, 5, 0, 0, 0))
    same_ring = [Entry("d1", pugs, x, EntryStatus.CLASS), Entry("d2", boxers, x, EntryStatus.CLASS)]
    assert exhibitor_conflicts(prog([(pugs, 1, 480), (boxers, 1, 540)], same_ring), 2).minutes == 0
    unregistered = [Entry("d1", pugs, y, EntryStatus.CLASS), Entry("d2", boxers, y, EntryStatus.CLASS)]
    assert exhibitor_conflicts(prog([(pugs, 1, 480), (boxers, 2, 480)], unregistered), 2).minutes == 0
    same_breed = [Entry("d1", pugs, x, EntryStatus.CLASS), Entry("d2", pugs, x, EntryStatus.CHAMPION)]
    assert exhibitor_conflicts(prog([(pugs, 1, 480)], same_breed), 2).minutes == 0


def _palm_springs():
    return read_program(ROOT / "data" / "parsed" / "bradshaw" / "2026-01-03-kennel-club-of-palm-springs")


def test_generator_matches_entry_counts_and_is_deterministic():
    p = _palm_springs()
    entries = generate(p.show, ExhibitorModel(seed=1))
    per_event = Counter((id(e.event), e.status) for e in entries)
    for e in p.show.events:
        if e.kind is EventKind.BREED and e.competition.value == "all-breed" and e.counts:
            assert per_event[(id(e), EntryStatus.CLASS)] == e.counts.before_bob
            assert per_event[(id(e), EntryStatus.CHAMPION)] == e.counts.champions
    again = generate(p.show, ExhibitorModel(seed=1))
    assert [(x.dog, x.exhibitor.id) for x in entries] == [(x.dog, x.exhibitor.id) for x in again]
    assert [(x.dog, x.exhibitor.id) for x in generate(p.show, ExhibitorModel(seed=2))] != [
        (x.dog, x.exhibitor.id) for x in entries
    ]


def test_generator_shape():
    p = _palm_springs()
    entries = generate(p.show)
    exs = {e.exhibitor for e in entries}
    teams = {x.team for x in exs if x.professional}
    assert len(teams) == round(len(entries) / 40)
    assert all(x.team for x in exs if x.professional)
    # a team member takes whole breeds: each (team, breed) has exactly one member
    members_per_team_breed = {}
    for e in entries:
        if e.exhibitor.team:
            members_per_team_breed.setdefault((e.exhibitor.team, id(e.event)), set()).add(e.exhibitor.id)
    assert all(len(m) == 1 for m in members_per_team_breed.values())
    team_sizes = Counter(x.team for x in exs if x.team)
    assert max(team_sizes.values()) > 1  # busy teams have assistants
    champs = [e for e in entries if e.status is EntryStatus.CHAMPION]
    pro_champ_share = sum(1 for e in champs if e.exhibitor.professional) / len(champs)
    assert 0.5 < pro_champ_share < 0.7
    breeds_per_owner = Counter()
    seen = set()
    for e in entries:
        if not e.exhibitor.professional and (e.exhibitor.id, id(e.event)) not in seen:
            seen.add((e.exhibitor.id, id(e.event)))
            breeds_per_owner[e.exhibitor.id] += 1
    multi = sum(1 for n in breeds_per_owner.values() if n > 1)
    assert 0 < multi < len(breeds_per_owner) * 0.3


def test_entries_csv_round_trip(tmp_path):
    p = _palm_springs()
    entries = generate(p.show)
    write_entries(entries, tmp_path)
    back = read_entries(tmp_path, p)
    assert [(e.dog, e.exhibitor, e.status, e.event is f.event) for e, f in zip(entries, back)] == [
        (e.dog, e.exhibitor, e.status, True) for e in entries
    ]


def test_conflicts_counted_by_breed_not_by_dog():
    x = Exhibitor("X", registered=True)
    pugs = ev("Pug", A, EntryCounts(10, 10, 3, 2, 0))  # 8:00–9:00, BOB from 8:48
    boxers = ev("Boxer", B, EntryCounts(5, 5, 0, 0, 0))  # ring 2, 8:30–8:54
    one_each = [Entry("d1", pugs, x, EntryStatus.CLASS), Entry("d2", boxers, x, EntryStatus.CLASS)]
    many = one_each + [
        Entry("d3", pugs, x, EntryStatus.CLASS),
        Entry("d4", pugs, x, EntryStatus.CHAMPION),
        Entry("d5", boxers, x, EntryStatus.CLASS),
    ]
    p1 = prog([(pugs, 1, 480), (boxers, 2, 510)], one_each)
    p2 = prog([(pugs, 1, 480), (boxers, 2, 510)], many)
    assert exhibitor_conflicts(p1, 2).minutes == exhibitor_conflicts(p2, 2).minutes == 24
    assert len(exhibitor_conflicts(p2, 2).conflicts) == 1


def test_breed_window_is_whole_event_if_any_class_dog():
    x = Exhibitor("X", registered=True)
    pugs = ev("Pug", A, EntryCounts(10, 10, 3, 2, 0))  # 8:00–9:00, BOB 8:48–9:00
    boxers = ev("Boxer", B, EntryCounts(5, 5, 0, 0, 0))  # ring 2, 8:00–8:24
    champ_only = [Entry("d1", pugs, x, EntryStatus.CHAMPION), Entry("d2", boxers, x, EntryStatus.CLASS)]
    assert exhibitor_conflicts(prog([(pugs, 1, 480), (boxers, 2, 480)], champ_only), 2).minutes == 0
    with_class = champ_only + [Entry("d3", pugs, x, EntryStatus.CLASS)]
    assert exhibitor_conflicts(prog([(pugs, 1, 480), (boxers, 2, 480)], with_class), 2).minutes == 24
