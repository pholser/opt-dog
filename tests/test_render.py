"""Renderers produce complete output for parsed and greedy programs."""

from pathlib import Path

from optdog.instance import instance_from_program, is_conformation
from optdog.io.program_csv import read_program
from optdog.render import render_html, render_text
from optdog.render.compare import render_compare
from optdog.render.__main__ import main
from optdog.solve.greedy import greedy_program

SHOW = Path(__file__).resolve().parents[1] / "data" / "parsed" / "bradshaw" / "2026-09-12-sir-francis-drake-kennel-club"
OBEDIENCE_SHOW = SHOW.parent / "2022-04-30-del-sur-kennel-club"


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


def test_compare_aligns_every_judge_and_fits_the_columns():
    human = read_program(SHOW)
    greedy = greedy_program(instance_from_program(human))
    text = render_compare(("Published", human), ("Greedy", greedy), width=70)
    assert "SUMMARY" in text and "planned finish (BIS end)" in text
    judges = {sb.judge.name for sb in human.blocks}
    body = text.split("Published", 2)[-1]
    for name in judges:
        assert body.count(f"{name}  (") == 2  # one heading per side, on the same line
        line = next(l for l in body.splitlines() if l.startswith(f"{name}  ("))
        assert line.count(name) == 2
    assert all(len(l.split(" │ ")[0]) <= 70 for l in text.splitlines() if " │ " in l)
    assert all(e.name in text for sb in human.blocks for e in sb.block.events)


def test_cli_compare_by_ring(tmp_path):
    out = tmp_path / "c.txt"
    assert main([str(SHOW), "--greedy", "--compare", "ring", "--out", str(out)]) == 0
    text = out.read_text()
    assert "RING 1" in text and " │ " in text


def test_compare_summarizes_conformation_and_carries_obedience_across():
    human = read_program(OBEDIENCE_SHOW)
    show = instance_from_program(human)
    text = render_compare(("Published", human), ("Greedy", greedy_program(show)))
    assert "(none)" not in text and "obedience/rally: not scheduled by the optimizer" in text
    assert "as published" in text
    obedience = [sb for sb in human.blocks if not is_conformation(sb)]
    assert obedience and all(e.name in text for sb in obedience for e in sb.block.events)
    row = next(l for l in text.splitlines() if l.lstrip().startswith("blocks  "))
    assert int(row.split()[1]) == len(human.blocks) - len(obedience)
