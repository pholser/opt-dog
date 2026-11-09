"""Planning from scratch: the problem format, the input checks and the CLI."""

from pathlib import Path

import pytest

from optdog.breeds import default_catalog
from optdog.domain import VARIETY_GROUPS, Block, Competition, EventKind, Group, JudgingEvent, Judge, Ring, RingSize, ScheduledBlock, Show
from optdog.instance import instance_from_program
from optdog.io.problem_csv import ProblemFormatError, read_problem, write_problem
from optdog.io.program_csv import read_program
from optdog.params import ShowParameters
from optdog.plan import check_problem, main
from optdog.solve.greedy import greedy_program
from optdog.validate import validate

CAT = default_catalog()
PARSED = sorted((Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw").iterdir())
A, B, G = Judge("Ms. A"), Judge("Mr. B"), Judge("Dr. G")


def small(**kw) -> Show:
    events = [JudgingEvent(b, EventKind.BREED, A, 18, CAT[b]) for b in ("Boxer", "Akita")]
    events += [JudgingEvent("Pug", EventKind.BREED, B, 20, CAT["Pug"])]
    rings = [Ring(1), Ring(2, RingSize.L), Ring(3, RingSize.S)]
    args = dict(name="T", date="2026-01-01", judges=[A, B], events=events, rings=rings,
                group_judges={Group.WORKING: G, Group.TOY: G}, bis_judge=G, group_rings=(rings[0],), start=480)
    return Show(**(args | kw))


def signature(show: Show):
    return ([(e.judge, e.name, e.kind, e.competition, e.breed, e.entries, e.counts) for e in show.events],
            [(r.number, r.size) for r in show.rings], [r.number for r in show.group_rings], show.judges, show.group_judges,
            show.bis_judge, show.start, show.lunch_window,
            [(sb.ring.number, sb.start, sb.judge, [e.name for e in sb.block.events]) for sb in show.pinned])


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_a_published_shows_inputs_survive_the_problem_format(d, tmp_path):
    show = instance_from_program(read_program(d))
    write_problem(show, tmp_path)
    back = read_problem(tmp_path)
    assert signature(back) == signature(show)
    pinned = {id(e) for sb in back.pinned for e in sb.block.events}
    assert pinned <= {id(e) for e in back.events}  # the same objects, so the solver treats them as pinned


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_the_greedy_gives_the_same_program_from_the_problem_files(d, tmp_path):
    show = instance_from_program(read_program(d))
    write_problem(show, tmp_path)
    def key(program):
        return sorted((sb.ring.number, sb.start, sb.judge.name, tuple(e.name for e in sb.block.events)) for sb in program.blocks)
    assert key(greedy_program(read_problem(tmp_path))) == key(greedy_program(show))


@pytest.mark.parametrize("d", PARSED, ids=lambda d: d.name)
def test_no_published_show_is_rejected_by_the_input_checks(d):
    assert [str(i) for i in check_problem(instance_from_program(read_program(d))) if i.error] == []


def codes(show):
    return [i.what for i in check_problem(show)]


def test_a_clean_problem_has_no_issues():
    assert check_problem(small()) == []


@pytest.mark.parametrize("change, expect", [
    (dict(bis_judge=None), "no Best in Show judge"),
    (dict(group_judges={Group.WORKING: G}), "no judge"),
    (dict(group_rings=()), "no group ring"),
    (dict(group_rings=(Ring(2, RingSize.L),)), "(H17)"),
    (dict(lunch_window=(720, 660)), "is empty"),
    (dict(rings=[Ring(1, RingSize.M)], group_rings=()), "(H16)"),
])
def test_input_errors_are_reported(change, expect):
    issues = check_problem(small(**change))
    assert any(i.error and expect in i.what for i in issues), [str(i) for i in issues]


def test_a_judge_over_the_daily_limit_is_a_warning():
    over = JudgingEvent("Boxer", EventKind.BREED, A, 180, CAT["Boxer"])
    issues = check_problem(small(events=[over]))
    assert [i.error for i in issues if "(V1)" in i.what] == [False]


def test_pinned_blocks_that_clash_are_errors():
    x = JudgingEvent("Boxer", EventKind.BREED, A, 20, CAT["Boxer"], competition=Competition.SPECIALTY)
    y = JudgingEvent("Akita", EventKind.BREED, B, 20, CAT["Akita"], competition=Competition.SPECIALTY)
    pins = [ScheduledBlock(Block(A, (x,)), Ring(2), 480), ScheduledBlock(Block(B, (y,)), Ring(2), 500)]
    assert any("overlap" in i.what and i.error for i in check_problem(small(events=[x, y], pinned=pins)))


def test_a_bad_reference_names_the_file_and_the_problem(tmp_path):
    write_problem(small(), tmp_path)
    text = (tmp_path / "events.csv").read_text().replace("Mr. B", "Mr. Nobody")
    (tmp_path / "events.csv").write_text(text)
    with pytest.raises(ProblemFormatError, match="Mr. Nobody"):
        read_problem(tmp_path)


def test_cli_plans_a_show_from_files_and_writes_a_valid_program(tmp_path, capsys):
    prob, out = tmp_path / "problem", tmp_path / "out"
    show = small(group_judges={g: G for g in VARIETY_GROUPS})
    write_problem(show, prob)
    assert main(["check", str(prob)]) == 0
    assert main(["solve", str(prob), "--out", str(out), "--html", str(tmp_path / "c.html"), "--time-limit", "10"]) == 0
    program = read_program(out)
    assert validate(program, ShowParameters()).violations == []
    assert (tmp_path / "c.html").stat().st_size > 0
    assert {sb.ring.number for sb in program.blocks if sb.judge.name == "Ms. A"} <= {1, 2}  # Boxer and Akita need L


def test_cli_stops_on_input_errors_without_solving(tmp_path, capsys):
    write_problem(small(bis_judge=None), tmp_path)
    assert main(["solve", str(tmp_path)]) == 2
    assert "no Best in Show judge" in capsys.readouterr().err
