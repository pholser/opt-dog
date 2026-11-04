"""Renderers produce complete output for parsed and greedy programs."""

from pathlib import Path

from optdog.instance import instance_from_program
from optdog.io.program_csv import read_program
from optdog.render import render_html, render_text
from optdog.render.__main__ import main
from optdog.solve.greedy import greedy_program

SHOW = Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw" / "2026-09-12-sir-francis-drake-kennel-club"


def test_text_lists_every_event_and_the_groups():
    prog = read_program(SHOW)
    text = render_text(prog)
    assert "GROUPS AND BEST IN SHOW" in text and "Best in Show" in text
    assert "(pinned: beginner-puppy)" in text
    assert all(e.name in text for sb in prog.blocks for e in sb.block.events)


def test_html_charts_every_block_for_both_programs():
    human = read_program(SHOW)
    greedy = greedy_program(instance_from_program(human))
    page = render_html([("Published", human), ("Greedy", greedy)])
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    # each block appears twice per program: ring view and judge view
    assert page.count('class="blk') == 2 * (len(human.blocks) + len(greedy.blocks))
    assert page.count('class="grp') >= 2 * 2 * 8  # 7 groups + BIS, ring and judge views, two programs


def test_cli_writes_html(tmp_path, capsys):
    out = tmp_path / "s.html"
    assert main([str(SHOW), "--greedy", "--html", str(out)]) == 0
    assert out.read_text().count("<section") == 2
