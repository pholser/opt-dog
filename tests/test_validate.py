"""Validator rules on small hand-built programs."""

from optdog.breeds import default_catalog
from optdog.domain import (
    Block, Break, EventKind, Group, GroupsPlan, JudgingEvent, JudgingProgram, Judge, Ring, ScheduledBlock, Show,
)
from optdog.params import ShowParameters
from optdog.validate import validate

CAT = default_catalog()
P0 = ShowParameters(overlap_tolerance_minutes=0)


def ev(breed, judge, n):
    return JudgingEvent(breed, EventKind.BREED, judge, n, CAT[breed])


def program(blocks, judges, breaks=(), plan=None, group_judges=None, bis=None):
    events = [e for sb in blocks for e in sb.block.events]
    rings = sorted({sb.ring for sb in blocks}, key=lambda r: r.number)
    show = Show("T", "2026-01-01", judges, events, rings, group_judges=group_judges or {}, bis_judge=bis,
                group_rings=plan.rings if plan else ())
    return JudgingProgram(show, blocks, list(breaks), plan)


def sb(judge, ring, start, *events):
    return ScheduledBlock(Block(judge, tuple(events)), Ring(ring), start)


def codes(prog, params=P0):
    return sorted(v.code for v in validate(prog, params).violations)


A, B = Judge("Ms. A"), Judge("Mr. B")


def test_clean_program_has_no_violations():
    prog = program([sb(A, 1, 480, ev("Pug", A, 25)), sb(A, 1, 540, ev("Boxer", A, 10))], [A])
    assert codes(prog) == []


def test_h1_long_multibreed_block_but_single_big_breed_is_exempt():
    long_block = sb(A, 1, 480, ev("Pug", A, 20), ev("Maltese", A, 15))  # 35 dogs = 84 min
    big_single = sb(A, 1, 570, ev("Golden Retriever", A, 40))  # 96 min, exempt (> 30)
    tail = sb(A, 1, 670, ev("Boxer", A, 5))
    assert codes(program([long_block, big_single, tail], [A])) == ["H1"]


def test_final_segment_over_50_is_a_soft_cost_unless_single_breed():
    first = sb(A, 1, 480, ev("Pug", A, 10))
    final = sb(A, 1, 510, ev("Maltese", A, 30), ev("Papillon", A, 25))  # 55 dogs, final segment
    res = validate(program([first, final], [A]), P0)
    assert res.violations == []
    assert res.soft["S8_final_segment_excess_dogs"] == 5
    final_single = sb(A, 1, 510, ev("Golden Retriever", A, 60))
    assert validate(program([first, final_single], [A]), P0).soft["S8_final_segment_excess_dogs"] == 0


def test_h9_requires_every_group_ring_free():
    g = Judge("Dr. G")
    ring1 = sb(A, 1, 480, ev("Pug", A, 25))  # ends 9:00
    ring2 = sb(B, 2, 480, ev("Golden Retriever", B, 50))  # ends 10:00, after groups start
    plan = GroupsPlan((Ring(1), Ring(2)), 9 * 60 + 30, (Group.TOY,), {Group.TOY: g}, g)
    prog = program([ring1, ring2], [A, B], plan=plan, group_judges={Group.TOY: g}, bis=g)
    assert "H9" in codes(prog)


def test_travel_margins_default_to_two_minutes():
    p = ShowParameters()
    assert p.judge_travel_minutes == 2.0 and p.exhibitor_travel_minutes == 2.0


def test_h3_ring_overlap_and_tolerance():
    a = sb(A, 1, 480, ev("Pug", A, 25))  # ends 540
    b = sb(B, 1, 535, ev("Boxer", B, 5))
    prog = program([a, b], [A, B])
    assert codes(prog) == ["H3"]
    assert codes(prog, ShowParameters(overlap_tolerance_minutes=5)) == []


def test_h4_judge_travel_between_rings():
    a = sb(A, 1, 480, ev("Pug", A, 25))  # ends 540
    b = sb(A, 2, 540, ev("Boxer", A, 5))  # no time to walk to ring 2
    assert codes(program([a, b], [A]), ShowParameters(overlap_tolerance_minutes=0, judge_travel_minutes=5)) == ["H4"]
    b_ok = sb(A, 2, 545, ev("Boxer", A, 5))
    assert codes(program([a, b_ok], [A]), ShowParameters(overlap_tolerance_minutes=0, judge_travel_minutes=5)) == []


def test_h6_lunch_required_after_five_hours():
    blocks = [sb(A, 1, 480 + 60 * i, ev(b, A, 25)) for i, b in enumerate(
        ["Pug", "Maltese", "Papillon", "Pomeranian", "Havanese", "Shih Tzu"])]  # 6 h, no gap
    assert "H6" in codes(program(blocks, [A]))
    with_lunch = blocks[:4] + [sb(A, 1, 12 * 60 + 45 + 60 * i, ev(b, A, 25)) for i, b in enumerate(["Havanese", "Shih Tzu"])]
    lunch = Break(A, Ring(1), 12 * 60, 45)
    assert "H6" not in codes(program(with_lunch, [A], breaks=[lunch]))


def test_group_timeline_waits_and_h7_h9():
    g_judge = Judge("Dr. G")
    sporting = sb(A, 1, 480, ev("Golden Retriever", A, 50))  # ends 600
    hound = sb(B, 2, 480, ev("Whippet", B, 25))  # ends 540
    plan = GroupsPlan((Ring(1),), 570, (Group.SPORTING, Group.HOUND), {Group.SPORTING: g_judge, Group.HOUND: g_judge}, g_judge)
    prog = program([sporting, hound], [A, B], plan=plan,
                   group_judges={Group.SPORTING: g_judge, Group.HOUND: g_judge}, bis=g_judge)
    found = codes(prog)
    assert "H7" in found  # Sporting published at 9:30 but Goldens finish at 10:00
    assert "H9" in found  # ring 1 (group ring) busy until 10:00
    tl = prog.group_timeline()
    assert tl.groups[0].start == 600 and tl.groups[0].wait == 30
    assert tl.groups[1].start == tl.groups[0].end  # Hound breeds already done
    assert tl.bis_start == tl.groups[1].end


def test_v1_judge_daily_limit():
    blocks = [sb(A, 1, 480, ev("Golden Retriever", A, 100)), sb(A, 1, 730, ev("Labrador Retriever", A, 80))]
    assert "V1" in codes(program(blocks, [A]))


def test_group_judge_needs_travel_margin_from_another_ring():
    g = Judge("Dr. G")  # judges Pugs in ring 3, then the Toy Group in ring 1
    pugs = sb(g, 3, 480, ev("Pug", g, 25))  # ends 9:00
    plan = GroupsPlan((Ring(1),), 9 * 60, (Group.TOY,), {Group.TOY: g}, g)
    prog = program([pugs], [g], plan=plan, group_judges={Group.TOY: g}, bis=g)
    tl = prog.group_timeline(judge_travel_minutes=2)
    assert tl.groups[0].ready == 9 * 60 + 2 and tl.groups[0].start == 9 * 60 + 2
    assert "H8" in codes(prog, ShowParameters(overlap_tolerance_minutes=0, judge_travel_minutes=2))
    # judging in the group ring itself needs no walk
    pugs_in_group_ring = sb(g, 1, 480, ev("Pug", g, 25))
    prog2 = program([pugs_in_group_ring], [g], plan=plan, group_judges={Group.TOY: g}, bis=g)
    assert prog2.group_timeline(judge_travel_minutes=2).groups[0].ready == 9 * 60
