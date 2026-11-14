"""Planning from scratch: the problem format, the input checks and the CLI."""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from optdog.breeds import default_catalog
from optdog.domain import VARIETY_GROUPS, Block, Competition, EventKind, Group, JudgingEvent, Judge, Ring, RingSize, ScheduledBlock, Show
from optdog.instance import instance_from_program
from optdog.io.problem_csv import ProblemFormatError, read_problem, write_problem
from optdog.conflicts import registered_window_pairs
from optdog.io.program_csv import read_entries, read_program
from optdog.params import ShowParameters
from optdog.plan import Issue, check_problem, main
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
    assert not (prob / "exhibitors.csv").exists()  # no exhibitor information: conflicts play no part
    assert main(["check", str(prob)]) == 0
    assert main(["solve", str(prob), "--out", str(out), "--html", str(tmp_path / "c.html"), "--time-limit", "10"]) == 0
    err = capsys.readouterr().err
    assert "warning" not in err and "exhibitor" not in err
    program = read_program(out)
    assert validate(program, ShowParameters()).violations == []
    assert (tmp_path / "c.html").stat().st_size > 0
    assert {sb.ring.number for sb in program.blocks if sb.judge.name == "Ms. A"} <= {1, 2}  # Boxer and Akita need L


def test_cli_stops_on_input_errors_without_solving(tmp_path, capsys):
    write_problem(small(bis_judge=None), tmp_path)
    assert main(["solve", str(tmp_path)]) == 2
    assert "no Best in Show judge" in capsys.readouterr().err


# ---------------------------------------------------------------- exhibitors and entries

SYNTH = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "bradshaw"


def entered(show: Show, *rows) -> Show:
    """rows: (dog, event name, exhibitor, status); fills the rest of each event with one-dog owners."""
    from optdog.domain import Entry, EntryStatus, Exhibitor

    events = {e.name: e for e in show.events}
    out = [Entry(dog, events[ev], x, EntryStatus(status)) for dog, ev, x, status in rows]
    for e in show.events:
        for i in range(e.entries - sum(1 for en in out if en.event is e)):
            out.append(Entry(f"{e.name}-{i}", e, Exhibitor(f"{e.name}-owner-{i}"), EntryStatus.CLASS))
    return replace(show, entries=out)


def test_extract_carries_exhibitors_and_entries_into_the_problem(tmp_path):
    day = next(d for d in PARSED if (SYNTH / d.name / "seed-0").exists())
    prob = tmp_path / "p"
    assert main(["extract", str(day), str(prob), "--entries", str(SYNTH / day.name / "seed-0")]) == 0
    back = read_problem(prob)
    program = read_program(day)
    program.show.entries = read_entries(SYNTH / day.name / "seed-0", program)
    want = instance_from_program(program).entries

    def key(entries):
        return sorted((en.dog, en.event.judge.name, en.event.name, en.exhibitor.id, en.exhibitor.registered,
                       en.exhibitor.team or "", en.status.value) for en in entries)
    assert key(back.entries) == key(want) and back.entries
    assert {id(en.event) for en in back.entries} <= {id(e) for e in back.events}
    assert registered_window_pairs(back.entries)  # the solver's conflict phase will run
    assert not [str(i) for i in check_problem(back) if "entries.csv" in i.what]


def test_team_members_are_separate_exhibitors(tmp_path):
    from optdog.domain import Exhibitor

    h, a = Exhibitor("H", True, True, "Team 1"), Exhibitor("A", True, True, "Team 1")
    show = entered(small(), ("d1", "Boxer", h, "champion"), ("d2", "Pug", a, "champion"))
    write_problem(show, tmp_path)
    back = read_problem(tmp_path)
    assert {en.exhibitor.id for en in back.entries if en.exhibitor.team == "Team 1"} == {"H", "A"}
    assert registered_window_pairs(back.entries) == []  # different people: no conflict between them


@pytest.mark.parametrize("edit, expect", [
    (lambda d: (d / "exhibitors.csv").unlink(), "entries.csv needs exhibitors.csv"),
    (lambda d: _sub(d / "entries.csv", ",X,", ",Nobody,"), "exhibitor 'Nobody' is not in exhibitors.csv"),
    (lambda d: _sub(d / "entries.csv", "champion", "special"), "status 'special'"),
    (lambda d: _sub(d / "entries.csv", "Pug", "Poodle"), "no event 'Poodle'"),
    (lambda d: _sub(d / "exhibitors.csv", "X,0,1", "X,0,yes"), "expected 0 or 1"),
])
def test_bad_entry_files_name_the_file_and_line(tmp_path, edit, expect):
    from optdog.domain import Exhibitor

    write_problem(entered(small(), ("d1", "Pug", Exhibitor("X", registered=True), "champion")), tmp_path)
    edit(tmp_path)
    with pytest.raises(ProblemFormatError, match=re.escape(expect)):
        read_problem(tmp_path)


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_entry_checks_warn_about_counts_duplicates_and_no_one_signed_up():
    from optdog.domain import Entry, EntryStatus, Exhibitor

    show = entered(small())
    assert check_problem(show) == [Issue(False, "exhibitors.csv",
                                         "no exhibitor is signed up for conflict resolution, so conflicts are not scheduled")]
    x = Exhibitor("X", registered=True)
    pug = next(e for e in show.events if e.name == "Pug")
    extra = replace(show, entries=show.entries + [Entry("Pug-0", pug, x, EntryStatus.CLASS)])
    issues = [i.what for i in check_problem(extra)]
    assert "21 rows in entries.csv for 20 entries" in issues
    assert "dog Pug-0 is entered 2 times" in issues
    assert not any(i.error for i in check_problem(extra))


def test_cli_solve_reports_conflicts_of_registered_exhibitors(tmp_path, capsys):
    from optdog.domain import Exhibitor

    x = Exhibitor("X", registered=True)
    show = entered(small(group_judges={g: G for g in VARIETY_GROUPS}), ("d1", "Boxer", x, "class"), ("d2", "Pug", x, "class"))
    prob = tmp_path / "problem"
    write_problem(show, prob)
    assert main(["solve", str(prob), "--out", str(tmp_path / "out"), "--time-limit", "10"]) == 0
    err = capsys.readouterr().err
    # the earliest finish needs Boxer and Pug judged side by side, so a few minutes remain
    assert re.search(r"exhibitor conflicts: \d+ min, [01] of 1 registered exhibitors", err), err
