"""The figures report and the baseline, from the saved results: complete, and true to the data."""

import re

import pytest

from optdog.analysis import baseline, figures
from optdog.params import ShowParameters
from optdog.solutions import load_day, parsed_days
from optdog.validate import validate


def test_figures_page_covers_every_day_and_states_the_data():
    p = ShowParameters()
    page = figures.render(p)
    days = figures.collect(p)
    assert page.startswith("<!doctype html>") and "http" not in page  # one file, works offline
    assert len(days) == len(parsed_days())
    assert all(d.label in page for d in days)
    short = sum(x < p.short_block_dogs for d in days for x in d.solver.sizes)
    assert "Short blocks: " in page and f"{short} of {sum(len(d.solver.sizes) for d in days)} in 3b" in page
    assert all(page.count(f'var(--s-{k})') >= len(days) for k in ("published", "greedy", "solver"))


def test_saved_schedules_are_valid_and_never_beat_the_bound():
    p = ShowParameters()
    for day in parsed_days():
        d = load_day(day)
        for res in d.results.values():
            v = validate(res.program, p)
            assert {x.code for x in v.violations} <= {"V1"}, [str(x) for x in v.violations]
            assert v.timeline.finish == pytest.approx(res.finish, abs=0.1)
            assert res.finish >= d.bound.value - 1e-6


def test_baseline_reports_every_day_from_saved_results():
    text = baseline.render()
    assert len(re.findall(r"^\| 20", text, flags=re.M)) == len(parsed_days())
