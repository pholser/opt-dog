"""Figures for the key metrics of the solved schedules, on all parsed days.

    python -m optdog.analysis.solve_all      # first, once: solves and saves every day
    python -m optdog.analysis.figures        # writes docs/figures.html

One self-contained page: inline SVG, no external resources, each figure with its data table.
Scores are for conformation only on both sides, and block sizes leave out pinned blocks, which
no scheduler controls. Rule slack in figure 5 describes the schedule; it does not prove what
relaxing a rule would gain.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from optdog.domain import JudgingProgram, fmt_time
from optdog.instance import conformation_only
from optdog.params import ShowParameters
from optdog.solutions import load_day, parsed_days
from optdog.solve.greedy import greedy_program
from optdog.validate import ValidationResult, lunch_ends, validate

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "figures.html"
STEP = "packing"
GRID = 5  # a rule binds when it leaves less than one time slot of slack
RULES = ("H7, H8", "H9", "H13", "H14")
RULE_NAMES = {
    "H7, H8": "First group ready", "H9": "Group rings clear", "H13": "Two groups' breeds done", "H14": "Every lunch over",
}
PROGRAMS = {"published": ("Published", "--s-published"), "greedy": ("Greedy", "--s-greedy"), "solver": ("3c", "--s-solver")}


def _e(x: object) -> str:
    return html.escape(str(x), quote=True)


def _clock(t: float) -> str:
    return fmt_time(int(round(t)))


# --------------------------------------------------------------------------- metrics


@dataclass
class Scored:
    finish: float
    group_start: int
    hard: list[str]
    switches: int
    short: int
    ring_changes: int
    idle: float
    sizes: list[int]
    slack: dict[str, float] = field(default_factory=dict)


@dataclass
class Day:
    label: str
    short: str
    bound: float
    bound_proven: bool
    solver_proven: bool
    published: Scored
    greedy: Scored
    formation: Scored
    ordering: Scored
    solver: Scored


def _label(name: str, show_name: str, iso: str) -> tuple[str, str]:
    d = date.fromisoformat(iso)
    club = show_name.replace(" Kennel Club", "").replace("Kennel Club of ", "").strip()
    initials = {"Greater Sierra Vista": "GSV", "Sir Francis Drake": "SFD"}.get(club, club)
    return f"{club} {d.month}/{d.day}/{d.year % 100}", f"{initials} {d.month}/{d.day}"


def _slack(program: JudgingProgram, res: ValidationResult, p: ShowParameters) -> dict[str, float]:
    """Minutes between what each rule requires and the published group start."""
    plan, tl = program.groups, res.timeline
    first = tl.groups[0]
    entered = sorted(s.breeds_done for s in tl.groups if program.show.group_entrants(s.group) > 0)
    need = min(p.groups_complete_before_start, len(entered))
    ring_free = [sb.end for sb in program.blocks if sb.ring in plan.rings]
    lunches = lunch_ends(program, p)
    needs = {"H7, H8": first.ready}  # its breeds done and its judge free
    if ring_free:
        needs["H9"] = max(ring_free) + p.group_ring_setup_minutes
    if need:
        needs["H13"] = entered[need - 1]
    if lunches:
        needs["H14"] = max(lunches.values())
    return {k: plan.start - v for k, v in needs.items()}


def score(program: JudgingProgram, pinned: set[int], p: ShowParameters) -> Scored:
    view = conformation_only(program)
    res = validate(view, p)
    movable = [sb for sb in view.blocks if id(sb) not in pinned]
    return Scored(
        finish=res.timeline.finish,
        group_start=view.groups.start,
        hard=sorted({v.code for v in res.violations}),
        switches=res.soft["S3_equipment_switches"],
        short=sum(sb.block.entries < p.short_block_dogs for sb in movable),
        ring_changes=res.soft["S2_ring_changes"] + res.soft["S2_ring_changes_at_lunch"],
        idle=res.soft["S5_judge_idle_min"],
        sizes=[sb.block.entries for sb in movable],
        slack=_slack(view, res, p),
    )


def collect(p: ShowParameters) -> list[Day]:
    days = []
    for path in parsed_days():
        d = load_day(path)
        pinned = {id(sb) for sb in d.show.pinned}
        label, short = _label(d.name, d.show.name, d.show.date)
        days.append(Day(
            label, short, d.bound.value, d.bound.optimal, d.results[STEP].optimal,
            score(d.human, pinned, p), score(greedy_program(d.show, p), pinned, p),
            score(d.results["formation"].program, pinned, p), score(d.results["ordering"].program, pinned, p),
            score(d.results[STEP].program, pinned, p),
        ))
    return days


# --------------------------------------------------------------------------- charts


def _ticks(lo: float, hi: float, steps=(1, 2, 5, 10, 20, 30, 50, 60, 100, 120, 200, 300, 500, 1000)) -> list[float]:
    span = max(hi - lo, 1)
    step = next((s for s in steps if span / s <= 6), steps[-1])
    first = math.floor(lo / step) * step
    return [first + i * step for i in range(int(math.ceil((hi - first) / step)) + 1)]


def dot_chart(rows: list[tuple[str, dict[str, float]]], names: dict[str, tuple[str, str]], fmt, ticks: list[float],
              title: str, width: int = 760, label_w: int = 190, row_h: int = 28) -> str:
    """One row per day, one dot per program, on one shared horizontal axis."""
    left, right, top, bottom = label_w, 24, 8, 26
    h = top + row_h * len(rows) + bottom
    lo, hi = ticks[0], ticks[-1]

    def x(v: float) -> float:
        return left + (v - lo) / (hi - lo) * (width - left - right)

    parts = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{_e(title)}">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top}" y2="{h - bottom}"/>'
                     f'<text class="tick" x="{x(t):.1f}" y="{h - 8}" text-anchor="middle">{_e(fmt(t))}</text>')
    for i, (label, values) in enumerate(rows):
        y = top + row_h * i + row_h / 2
        parts.append(f'<text class="row" x="{left - 12}" y="{y + 4:.1f}" text-anchor="end">{_e(label)}</text>')
        vs = list(values.values())
        if len(vs) > 1:
            parts.append(f'<line class="link" x1="{x(min(vs)):.1f}" x2="{x(max(vs)):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
        tip = label + " · " + " · ".join(f"{names[k][0]} {fmt(v)}" for k, v in values.items())
        for k in values:
            v = values[k]
            parts.append(f'<g data-tip="{_e(tip)}"><circle class="hit" cx="{x(v):.1f}" cy="{y:.1f}" r="11"/>'
                         f'<circle class="dot" style="fill:var({names[k][1]})" cx="{x(v):.1f}" cy="{y:.1f}" r="5"/></g>')
    parts.append("</svg>")
    return "".join(parts)


def legend(keys: list[str]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var({PROGRAMS[k][1]})"></i>{_e(PROGRAMS[k][0])}</span>' for k in keys) + "</div>"


def _bins(sizes: list[int], width: int = 5, last: int = 60) -> list[int]:
    counts = [0] * (last // width + 1)
    for s in sizes:
        counts[min(s, last) // width] += 1
    return counts


def histogram(sizes: list[int], ymax: int, var: str, title: str, threshold: int) -> str:
    w, h, left, right, top, bottom = 760, 170, 44, 16, 20, 26
    counts = _bins(sizes)
    n = len(counts)
    band = (w - left - right) / n
    bar = min(24, band - 2)

    def y(v: float) -> float:
        return top + (1 - v / ymax) * (h - top - bottom)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{_e(title)}">']
    for t in _ticks(0, ymax):
        if t > ymax:
            continue
        parts.append(f'<line class="grid" x1="{left}" x2="{w - right}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="tick" x="{left - 8}" y="{y(t) + 4:.1f}" text-anchor="end">{t:g}</text>')
    for i, c in enumerate(counts):
        cx = left + band * (i + .5)
        name = f"{i * 5}–{i * 5 + 4}" if i < n - 1 else f"{i * 5}+"
        parts.append(f'<text class="tick" x="{cx:.1f}" y="{h - 8}" text-anchor="middle">{name}</text>')
        if c == 0:
            continue
        x0, x1, yt, yb = cx - bar / 2, cx + bar / 2, y(c), y(0)
        r = min(4, yb - yt)
        d = f"M{x0:.1f} {yb:.1f}V{yt + r:.1f}Q{x0:.1f} {yt:.1f} {x0 + r:.1f} {yt:.1f}H{x1 - r:.1f}Q{x1:.1f} {yt:.1f} {x1:.1f} {yt + r:.1f}V{yb:.1f}Z"
        tip = f"{title}: {c} blocks of {name} dogs"
        parts.append(f'<g data-tip="{_e(tip)}"><rect class="hit" x="{cx - band / 2:.1f}" y="{top}" width="{band:.1f}" height="{h - top - bottom}"/>'
                     f'<path style="fill:var({var})" d="{d}"/></g>')
    tx = left + band * (threshold / 5)
    parts.append(f'<line class="limit" x1="{tx:.1f}" x2="{tx:.1f}" y1="{top - 8}" y2="{h - bottom}"/>'
                 f'<text class="row" x="{tx + 6:.1f}" y="{top}">under {threshold} dogs is short</text></svg>')
    return "".join(parts)


def table(head: list[str], rows: list[list[object]], summary: str = "Data table") -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<details><summary>{_e(summary)}</summary><div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div></details>'


# --------------------------------------------------------------------------- the page


CSS = """
:root{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6e69;--grid:#e1e0d9;
--link:#c3c2b7;--ring:#fcfcfb;--s-published:#eb6834;--s-greedy:#1baf7a;--s-solver:#2a78d6}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#a3a29a;--grid:#2c2c2a;--link:#52514e;--ring:#1a1a19;--s-published:#d95926;--s-greedy:#199e70;--s-solver:#3987e5}}
:root[data-theme="dark"]{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a29a;--grid:#2c2c2a;
--link:#52514e;--ring:#1a1a19;--s-published:#d95926;--s-greedy:#199e70;--s-solver:#3987e5}
*{box-sizing:border-box}body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;line-height:1.2;margin:0 0 8px}
h2{font-size:17px;line-height:1.3;margin:0 0 4px}p{margin:0 0 12px;color:var(--ink2)}.lede{max-width:68ch}
section{background:var(--surface);border:1px solid rgba(127,127,127,.18);border-radius:8px;padding:20px;margin-top:20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--surface);border:1px solid rgba(127,127,127,.18);border-radius:8px;padding:14px 16px}
.tile b{display:block;font-size:30px;line-height:1.2;font-weight:600}.tile span{color:var(--ink2);font-size:13px}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:8px 0;font-size:13px;color:var(--ink2)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
svg{display:block;width:100%;height:auto;font:12px system-ui,-apple-system,sans-serif}
.grid{stroke:var(--grid);stroke-width:1}.tick{fill:var(--muted);font-size:11px}.row{fill:var(--ink2)}
.link{stroke:var(--link);stroke-width:2;stroke-linecap:round}.dot{stroke:var(--ring);stroke-width:2}.hit{fill:transparent}
.limit{stroke:var(--ink2);stroke-width:1}.wide{overflow-x:auto}.wide svg{min-width:620px}.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px;margin-top:8px}
.panels h3,.sub{font-size:14px;margin:12px 0 4px}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:right}td{white-space:nowrap}th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:600}details{margin-top:12px;font-size:13px}summary{cursor:pointer;color:var(--ink2)}
.binds{font-weight:600}.notes{font-size:13px}.notes li{margin-bottom:4px;color:var(--ink2)}
#tip{position:fixed;display:none;max-width:320px;padding:6px 10px;border-radius:6px;background:var(--ink);color:var(--surface);font-size:12px;pointer-events:none;z-index:9}
"""

JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest&&e.target.closest('[data-tip]');
 if(!t){tip.style.display='none';return}
 tip.textContent=t.dataset.tip;tip.style.display='block';
 const w=tip.offsetWidth,h=tip.offsetHeight;let x=e.clientX+14,y=e.clientY+14;
 if(x+w>innerWidth-8)x=Math.max(8,e.clientX-w-14);if(y+h>innerHeight-8)y=Math.max(8,e.clientY-h-14);
 tip.style.left=x+'px';tip.style.top=y+'px'});
"""


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _hard(s: Scored) -> list[str]:
    return [c for c in s.hard if c != "V1"]


def render(params: ShowParameters | None = None) -> str:
    p = params or ShowParameters()
    days = collect(p)
    n = len(days)

    # headline figures
    gap_pub = _mean([d.published.finish - d.bound for d in days])
    gap_gre = _mean([d.greedy.finish - d.bound for d in days])
    gap_sol = _mean([d.solver.finish - d.bound for d in days])
    earlier = _mean([d.published.finish - d.solver.finish for d in days])
    clean_pub = sum(not _hard(d.published) for d in days)
    clean_sol = sum(not _hard(d.solver) for d in days)
    proven = sum(d.solver_proven for d in days)
    tiles = [
        (f"{earlier:.0f} min", "earlier finish than published, on average"),
        (f"{gap_sol:.0f} min", f"above the lower bound, on average (published {gap_pub:.0f})"),
        (f"{clean_sol} of {n}", f"days with no hard-rule violations (published {clean_pub})"),
        (f"{proven} of {n}", "days proven optimal for 3c's rules"),
    ]

    # 1. finish against the bound
    rows = [(d.label, {"published": d.published.finish - d.bound, "greedy": d.greedy.finish - d.bound,
                       "solver": d.solver.finish - d.bound}) for d in days]
    top = max(max(v.values()) for _, v in rows)
    fig1 = dot_chart(rows, PROGRAMS, lambda v: f"{max(v, 0):.0f}", _ticks(0, top), "Minutes above the lower bound on show finish")
    t1 = table(["Day", "Lower bound", "Published", "Greedy", "3a", "3b", "3c", "3c above bound"],
               [[d.label, _clock(d.bound), _clock(d.published.finish), _clock(d.greedy.finish), _clock(d.formation.finish),
                 _clock(d.ordering.finish), _clock(d.solver.finish), f"{max(d.solver.finish - d.bound, 0):.0f}"] for d in days])

    # 2. group start
    rows = [(d.label, {"published": float(d.published.group_start), "solver": float(d.solver.group_start)}) for d in days]
    lo = min(min(v.values()) for _, v in rows)
    hi = max(max(v.values()) for _, v in rows)
    two = {k: PROGRAMS[k] for k in ("published", "solver")}
    fig2 = dot_chart(rows, two, _clock, [t * 60 for t in range(int(lo // 60), int(-(-hi // 60)) + 1)], "First group's start time")
    t2 = table(["Day", "Published", "3c", "Earlier by (min)"],
               [[d.label, _clock(d.published.group_start), _clock(d.solver.group_start),
                 d.published.group_start - d.solver.group_start] for d in days])

    # 3. schedule quality
    metrics = [("Equipment switches", "switches"), ("Short blocks", "short"), ("Judge ring changes", "ring_changes"),
               ("Judge idle minutes", "idle")]
    panels = []
    for name, attr in metrics:
        rows = [(d.short, {"published": float(getattr(d.published, attr)), "solver": float(getattr(d.solver, attr))}) for d in days]
        hi = max(max(v.values()) for _, v in rows)
        svg = dot_chart(rows, two, lambda v: f"{v:g}", _ticks(0, hi), name, width=380, label_w=116, row_h=24)
        panels.append(f"<div><h3>{_e(name)}</h3>{svg}</div>")
    t3 = table(["Day"] + [f"{m[0]}: {who}" for m in metrics for who in ("published", "3c")],
               [[d.label] + [f"{getattr(s, m[1]):g}" for m in metrics for s in (d.published, d.solver)] for d in days])
    totals = {attr: (sum(getattr(d.published, attr) for d in days), sum(getattr(d.solver, attr) for d in days)) for _, attr in metrics}
    worse = {attr: sum(getattr(d.solver, attr) > getattr(d.published, attr) for d in days) for _, attr in metrics}
    start_gain = _mean([d.published.group_start - d.solver.group_start for d in days])

    # 4. block sizes
    sp = [s for d in days for s in d.published.sizes]
    ss = [s for d in days for s in d.solver.sizes]
    ymax = max(_bins(sp) + _bins(ss))
    ymax = int(-(-ymax // 10) * 10)
    fig4 = (f'<h3 class="sub">Published: {len(sp)} blocks</h3>' + histogram(sp, ymax, "--s-published", "Published", p.short_block_dogs)
            + f'<h3 class="sub">3c: {len(ss)} blocks</h3>' + histogram(ss, ymax, "--s-solver", "3c", p.short_block_dogs))
    median = lambda xs: sorted(xs)[len(xs) // 2]
    short_pub, short_sol = sum(x < p.short_block_dogs for x in sp), sum(x < p.short_block_dogs for x in ss)
    t4 = table(["Dogs per block", "Published", "3c"],
               [[f"{i * 5}–{i * 5 + 4}" if i < len(_bins(sp)) - 1 else f"{i * 5}+", a, b]
                for i, (a, b) in enumerate(zip(_bins(sp), _bins(ss)))])

    # 5. what holds the first group
    head = "".join(f"<th>{_e(RULE_NAMES[r])}<br>{r}</th>" for r in RULES)
    body = []
    counts = dict.fromkeys(RULES, 0)
    for d in days:
        cells = []
        for r in RULES:
            v = d.solver.slack.get(r)
            if v is None:
                cells.append("<td>–</td>")
            elif v < GRID:
                counts[r] += 1
                cells.append('<td class="binds">● binds</td>')
            else:
                cells.append(f"<td>{v:.0f}</td>")
        body.append(f"<tr><td>{_e(d.label)}</td><td>{_clock(d.solver.group_start)}</td>{''.join(cells)}</tr>")
    fig5 = (f'<div class="scroll"><table><thead><tr><th>Day</th><th>3c group start</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')
    tally = "; ".join(f"{RULE_NAMES[r]} ({r}) on {counts[r]}" for r in sorted(RULES, key=lambda r: -counts[r]) if counts[r])
    free = sum(all(d.solver.slack.get(r, GRID) >= GRID for r in RULES) for d in days)
    unbound = f"No rule binds on {free} of {n} days. " if free else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Schedule Metrics</title><style>{CSS}</style></head><body><main>
<h1>Schedule metrics: published programs against the optimizer</h1>
<p class="lede">{n} real show days. Published is each club's program, 3c is the optimizer's schedule for the same entries, judges and rings, and Greedy is a simple rule-based baseline.</p>
<div class="tiles">{''.join(f'<div class="tile"><b>{_e(a)}</b><span>{_e(b)}</span></div>' for a, b in tiles)}</div>

<section><h2>Show finish against the lower bound</h2>
<p>Minutes above the lower bound on show finish. No valid schedule can finish below 0. On average: published {gap_pub:.0f}, greedy {gap_gre:.0f}, 3c {gap_sol:.0f}.</p>
{legend(['published', 'greedy', 'solver'])}<div class="wide">{fig1}</div>{t1}</section>

<section><h2>First group's start</h2>
<p>When the first group starts, published against 3c: {start_gain:.0f} minutes earlier in 3c on average.</p>
{legend(['published', 'solver'])}<div class="wide">{fig2}</div>{t2}</section>

<section><h2>Schedule quality</h2>
<p>Lower is better on all four. Totals over the {n} days, published → 3c: {totals['switches'][0]:g} → {totals['switches'][1]:g} equipment switches,
{totals['short'][0]:g} → {totals['short'][1]:g} short blocks, {totals['ring_changes'][0]:g} → {totals['ring_changes'][1]:g} judge ring changes,
{totals['idle'][0]:.0f} → {totals['idle'][1]:.0f} judge idle minutes.
Days on which 3c is worse than published: {worse['switches']} for switches, {worse['short']} for short blocks, {worse['ring_changes']} for ring changes, {worse['idle']} for idle.</p>
{legend(['published', 'solver'])}<div class="panels">{''.join(panels)}</div>{t3}</section>

<section><h2>Block sizes</h2>
<p>Dogs per block, all {n} days pooled, same scale on both, pinned blocks left out. Median {median(sp)} dogs published, {median(ss)} in 3c.
Short blocks: {short_pub} of {len(sp)} published ({100 * short_pub / len(sp):.0f}%), {short_sol} of {len(ss)} in 3c ({100 * short_sol / len(ss):.0f}%).</p>
<div class="wide">{fig4}</div>{t4}</section>

<section><h2>What holds up the first group in 3c</h2>
<p>Minutes of slack each rule leaves before 3c's group start. A rule binds when it leaves under {GRID} minutes, one time slot.
Days binding: {_e(tally)}. {unbound}This describes these schedules: it doesn't show how much relaxing a rule would gain.</p>
{fig5}</section>

<section><h2>Reading these numbers</h2><ul class="notes">
<li>Times are planned times at the judges' rates. Published programs are re-timed the same way, so they show what was planned, not what happened on the day.</li>
<li>Group and Best in Show durations are upper bounds, so finish times run late. Differences between schedules are more reliable than clock times.</li>
<li>Scores cover conformation only on both sides. Obedience and rally run in their own rings, outside the optimizer.</li>
<li>Hard-rule counts leave out V1, a judge over the daily entry limit, which is an input problem no schedule can fix.</li>
<li>Exhibitor conflicts are left out because they rest on synthetic entries.</li>
<li>The optimizer gives up to {p.short_block_finish_minutes:g} minutes of finish to avoid a short block. 3a and 3b are the earlier steps: 3b chooses each judge's group order, and 3c also packs the breeds inside each run into full blocks.</li>
<li>"Proven optimal" means nothing better exists, counting {p.short_block_finish_minutes:g} minutes per short block, under 3c's own rules: the block cap, the time grid, each judge keeping their groups together, and the packed breed order. A schedule with a freer order could still beat it.</li>
</ul></section>
</main><div id="tip"></div><script>{JS}</script></body></html>
"""


def main() -> None:
    OUT.write_text(render())
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
