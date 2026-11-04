"""Render a JudgingProgram for people: a text printout and an HTML ring/time chart.

    python -m optdog.render data/parsed/bradshaw/<show>              # text to stdout
    python -m optdog.render data/parsed/bradshaw/<show> --html out.html --greedy
"""

from optdog.render.html import render_html
from optdog.render.text import render_text

__all__ = ["render_html", "render_text"]
