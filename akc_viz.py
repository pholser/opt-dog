"""
akc_viz.py — Interactive schedule visualization
================================================
Generates a plotly HTML calendar/Gantt chart from a SolveResult.

Each column is one ring; the Y-axis is time of day (earliest at top).
Each judge's blocks are drawn in a unique colour; hover for the full
breed list with entry counts.

Standalone usage
----------------
    python akc_viz.py show.xlsx [schedule.html] [--time-limit N] [--solver NAME]

Library usage
-------------
    from akc_viz import generate_chart
    generate_chart(result, "schedule.html")
"""

from __future__ import annotations

import math
import sys

# 12-colour Tableau-style palette, readable on white backgrounds
_PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2",
    "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7",
    "#9C755F", "#BAB0AC", "#D37295", "#A0CBE8",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_chart(result, output_path: str) -> None:
    """Write the schedule as a standalone interactive HTML chart."""
    html = _build_chart(result)
    with open(output_path, "w") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

def _slot_to_min(slot: int, slot_minutes: int) -> float:
    return float(slot * slot_minutes)


def _fmt_hhmm(total_minutes: float) -> str:
    h = int(total_minutes) // 60
    m = int(total_minutes) % 60
    return f"{h:02d}:{m:02d}"


def _build_chart(result) -> str:
    try:
        import plotly.graph_objects as go
    except ImportError:
        raise ImportError("plotly is required for visualization: pip install plotly")

    show     = result.show
    p        = show.params
    slot_min = p.slot_minutes

    # ── Ring columns ─────────────────────────────────────────────────────────

    def _ring_key(r):
        try:
            return (0, int(r))
        except ValueError:
            return (1, r)

    breed_rings = sorted({ss.ring_id for ss in result.segments}, key=_ring_key)
    arena_rings = sorted({gs.ring_id for gs in result.groups},   key=_ring_key)
    all_rings   = breed_rings + [r for r in arena_rings if r not in breed_rings]
    ring_idx    = {rid: i for i, rid in enumerate(all_rings)}
    n_rings     = len(all_rings)

    # ── Judge → colour ────────────────────────────────────────────────────────

    judge_color = {
        jid: _PALETTE[i % len(_PALETTE)]
        for i, jid in enumerate(sorted(show.judges))
    }

    # ── Gather all slot extents for axis bounds ───────────────────────────────

    all_slots = (
        [ss.start_slot for ss in result.segments] +
        [ss.end_slot   for ss in result.segments] +
        [gs.start_slot for gs in result.groups]   +
        [gs.end_slot   for gs in result.groups]   +
        [result.bis_start_slot,
         result.bis_start_slot + p.slots(20)]
    )
    y_min_m = _slot_to_min(min(all_slots), slot_min) - 30.0
    y_max_m = _slot_to_min(max(all_slots), slot_min) + 30.0

    # ── Build shapes + per-judge hover data ───────────────────────────────────

    shapes: list = []

    # ── Lunch window: full-width background band ──────────────────────────────

    lunch_y0_m = _slot_to_min(p.lunch_start_slot, slot_min)
    lunch_y1_m = _slot_to_min(p.lunch_end_slot,   slot_min)
    shapes.append(dict(
        type="rect",
        x0=-0.55, x1=n_rings - 0.45,
        y0=lunch_y0_m, y1=lunch_y1_m,
        fillcolor="rgba(255, 200, 50, 0.12)",
        line=dict(color="rgba(180, 140, 0, 0.35)", width=1, dash="dot"),
        layer="below",
    ))

    # judge_id → (x_centres, y_centres, hover_html_strings)
    hover_data: dict = {jid: ([], [], []) for jid in show.judges}

    def _add_block(xi, y0_m, y1_m, jid, hover_html):
        shapes.append(dict(
            type="rect",
            x0=xi - 0.45, x1=xi + 0.45,
            y0=y0_m,       y1=y1_m,
            fillcolor=judge_color.get(jid, "#888"),
            opacity=0.85,
            line=dict(color="rgba(0,0,0,0.35)", width=1),
            layer="below",
        ))
        xs, ys, ts = hover_data[jid]
        xs.append(float(xi))
        ys.append((y0_m + y1_m) / 2.0)
        ts.append(hover_html)

    def _breed_lines(breed_ids):
        lines = []
        for bid in breed_ids:
            b = show.breeds.get(bid)
            if b is None:
                continue
            name = b.breed_name
            if getattr(b, "variety", None):
                name += f" ({b.variety})"
            lines.append(f"&nbsp;&nbsp;{name}: {b.n_total}")
        return lines

    # Breed segments
    for ss in result.segments:
        xi    = ring_idx[ss.ring_id]
        y0_m  = _slot_to_min(ss.start_slot, slot_min)
        y1_m  = _slot_to_min(ss.end_slot,   slot_min)
        jname = show.judges[ss.judge_id].judge_name
        blines = _breed_lines(ss.breed_ids)
        hover = (
            f"<b>{jname}</b><br>"
            f"Ring {ss.ring_id} &nbsp;·&nbsp; "
            f"{_fmt_hhmm(y0_m)} – {_fmt_hhmm(y1_m)}<br>"
            f"{ss.n_dogs} dogs<br>"
            + "<br>".join(blines[:30])
            + ("<br>&nbsp;&nbsp;…" if len(blines) > 30 else "")
        )
        _add_block(xi, y0_m, y1_m, ss.judge_id, hover)

    # Group events
    for gs in result.groups:
        grp   = show.groups[gs.group_id]
        xi    = ring_idx.get(gs.ring_id, n_rings - 1)
        y0_m  = _slot_to_min(gs.start_slot, slot_min)
        y1_m  = _slot_to_min(gs.end_slot,   slot_min)
        jname = show.judges[grp.judge_id].judge_name
        hover = (
            f"<b>{grp.group_name} Group</b><br>"
            f"Judge: {jname}<br>"
            f"Ring {gs.ring_id} &nbsp;·&nbsp; "
            f"{_fmt_hhmm(y0_m)} – {_fmt_hhmm(y1_m)}<br>"
            f"{gs.n_breeds} breeds"
        )
        _add_block(xi, y0_m, y1_m, grp.judge_id, hover)

    # BIS — slightly thicker border to distinguish it
    bis_y0  = _slot_to_min(result.bis_start_slot, slot_min)
    bis_y1  = bis_y0 + float(p.slots(20) * slot_min)
    bis_xi  = ring_idx.get(
        result.groups[0].ring_id if result.groups else all_rings[-1],
        n_rings - 1,
    )
    bis_jid   = show.bis_judge_id
    bis_jname = show.judges[bis_jid].judge_name

    shapes.append(dict(
        type="rect",
        x0=bis_xi - 0.45, x1=bis_xi + 0.45,
        y0=bis_y0, y1=bis_y1,
        fillcolor=judge_color.get(bis_jid, "#888"),
        opacity=0.95,
        line=dict(color="rgba(0,0,0,0.7)", width=2),
        layer="below",
    ))
    xs, ys, ts = hover_data[bis_jid]
    xs.append(float(bis_xi))
    ys.append((bis_y0 + bis_y1) / 2.0)
    ts.append(
        f"<b>Best in Show</b><br>"
        f"Judge: {bis_jname}<br>"
        f"{_fmt_hhmm(bis_y0)}"
    )

    # ── Breed boundary tick marks within each segment ─────────────────────────
    # A thin horizontal line at each inter-breed transition shows the internal
    # slot structure without cluttering the chart.

    for ss in result.segments:
        xi       = ring_idx[ss.ring_id]
        cur_slot = ss.start_slot
        for bid in ss.breed_ids[:-1]:   # no line after the last breed
            b = show.breeds.get(bid)
            if b is None:
                continue
            cur_slot  += getattr(b, "delta_total_slots", 0)
            boundary_m = _slot_to_min(cur_slot, slot_min)
            shapes.append(dict(
                type="line",
                x0=xi - 0.43, x1=xi + 0.43,
                y0=boundary_m, y1=boundary_m,
                line=dict(color="rgba(255,255,255,0.6)", width=1),
                layer="above",
            ))

    # ── Text annotations inside blocks (judge surname + dog count) ────────────
    # Only for blocks ≥ 30 min tall to avoid overflow.

    annotations = []

    # Lunch window label — pinned to left edge of plot area (xref="paper")
    annotations.append(dict(
        x=0, xref="paper",
        y=(lunch_y0_m + lunch_y1_m) / 2.0, yref="y",
        text=(f"<b>Lunch window</b><br>"
              f"{_fmt_hhmm(lunch_y0_m)}–{_fmt_hhmm(lunch_y1_m)}"),
        showarrow=False,
        font=dict(size=7, color="rgba(140, 100, 0, 0.9)"),
        align="left",
        xanchor="left",
    ))

    for ss in result.segments:
        xi   = ring_idx[ss.ring_id]
        y0_m = _slot_to_min(ss.start_slot, slot_min)
        y1_m = _slot_to_min(ss.end_slot,   slot_min)
        if y1_m - y0_m < 30:
            continue
        surname = show.judges[ss.judge_id].judge_name.split()[-1]
        annotations.append(dict(
            x=float(xi),
            y=(y0_m + y1_m) / 2.0,
            text=f"<b>{surname}</b><br>{ss.n_dogs}",
            showarrow=False,
            font=dict(size=8, color="white"),
            align="center",
            xref="x", yref="y",
        ))

    # Group event labels
    for gs in result.groups:
        grp  = show.groups[gs.group_id]
        xi   = ring_idx.get(gs.ring_id, n_rings - 1)
        y0_m = _slot_to_min(gs.start_slot, slot_min)
        y1_m = _slot_to_min(gs.end_slot,   slot_min)
        if y1_m - y0_m < 15:
            continue
        annotations.append(dict(
            x=float(xi),
            y=(y0_m + y1_m) / 2.0,
            text=f"<b>{grp.group_name}</b>",
            showarrow=False,
            font=dict(size=8, color="white"),
            align="center",
            xref="x", yref="y",
        ))

    # BIS annotation
    annotations.append(dict(
        x=float(bis_xi),
        y=(bis_y0 + bis_y1) / 2.0,
        text="<b>BIS</b>",
        showarrow=False,
        font=dict(size=8, color="white"),
        align="center",
        xref="x", yref="y",
    ))

    # ── One invisible scatter trace per judge (hover tooltip + legend) ────────

    traces = []
    for jid in sorted(show.judges):
        xs, ys, ts = hover_data[jid]
        if not xs:
            continue
        traces.append(go.Scatter(
            x=xs, y=ys,
            mode="markers",
            marker=dict(
                color=judge_color[jid],
                size=12,
                opacity=0.01,
                symbol="square",
            ),
            name=show.judges[jid].judge_name,
            text=ts,
            hovertemplate="%{text}<extra></extra>",
        ))

    # ── Y-axis tick marks (one per hour) ─────────────────────────────────────

    start_hr  = int(y_min_m) // 60
    end_hr    = math.ceil(y_max_m / 60)
    tick_vals = [h * 60 for h in range(start_hr, end_hr + 1)]
    tick_text = [_fmt_hhmm(v) for v in tick_vals]

    # ── X-axis column labels ──────────────────────────────────────────────────

    col_labels = []
    for r in all_rings:
        if r in arena_rings and r not in breed_rings:
            col_labels.append(f"Arena ({r})")
        else:
            try:
                col_labels.append(f"Ring {int(r)}")
            except ValueError:
                col_labels.append(r)

    # ── Chart title ───────────────────────────────────────────────────────────

    total_dogs = sum(ss.n_dogs for ss in result.segments)
    try:
        show_date = str(p.show_date)
    except Exception:
        show_date = ""

    title_txt = (
        f"{p.club_name}  ·  {show_date}  ·  "
        f"{total_dogs} dogs  ·  "
        f"BIS: {_fmt_hhmm(bis_y0)}  ·  "
        f"gap {result.gap * 100:.1f}%"
    )

    # ── Assemble figure ───────────────────────────────────────────────────────

    fig = go.Figure(
        data=traces,
        layout=go.Layout(
            title=dict(text=title_txt, font=dict(size=13)),
            xaxis=dict(
                tickmode="array",
                tickvals=list(range(n_rings)),
                ticktext=col_labels,
                range=[-0.6, n_rings - 0.4],
                side="top",
                showgrid=True,
                gridcolor="rgba(0,0,0,0.08)",
                fixedrange=False,
            ),
            yaxis=dict(
                tickmode="array",
                tickvals=tick_vals,
                ticktext=tick_text,
                range=[y_max_m, y_min_m],   # inverted: morning at top
                showgrid=True,
                gridcolor="rgba(0,0,0,0.08)",
                zeroline=False,
            ),
            shapes=shapes,
            annotations=annotations,
            plot_bgcolor="white",
            paper_bgcolor="white",
            hovermode="closest",
            legend=dict(
                title=dict(text="Judge"),
                orientation="v",
                x=1.01, y=1.0,
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="rgba(0,0,0,0.15)",
                borderwidth=1,
            ),
            margin=dict(l=70, r=220, t=120, b=30),
            height=max(700, int((y_max_m - y_min_m) * 2.8)),
            width=max(900, n_rings * 85 + 280),
        ),
    )

    return fig.to_html(full_html=True, include_plotlyjs="cdn")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    from akc_preprocessing import load_show
    from akc_cpsat import solve_show
    from akc_schedule import SolveParams

    parser = argparse.ArgumentParser(
        description="Solve an AKC show and generate an interactive schedule chart."
    )
    parser.add_argument("show_file",
                        help="Path to show workbook (.xlsx)")
    parser.add_argument("output_file", nargs="?", default=None,
                        help="Output HTML path. Default: <show_file>_schedule.html")
    parser.add_argument("--time-limit", type=float, default=600,
                        dest="time_limit")
    parser.add_argument("--gap",        type=float, default=0.005,
                        help="Optimality gap tolerance (default 0.005)")
    parser.add_argument("--solver",     default="cpsat",
                        help="Solver: cpsat (default)")
    parser.add_argument("--slot-minutes", type=int, default=None,
                        dest="slot_minutes",
                        help="Override workbook time_slot_minutes value")
    parser.add_argument("--ring-switch-penalty", type=float, default=0.0,
                        dest="ring_switch_penalty",
                        help="Minutes of BIS time each ring switch costs (default 0 = tiebreaker only)")
    parser.add_argument("--forbid-ring-switches", action="store_true",
                        dest="forbid_ring_switches",
                        help="Hard constraint: no judge may change rings between segments")
    args = parser.parse_args()

    print(f"Loading {args.show_file} ...", file=sys.stderr)
    show   = load_show(args.show_file, slot_minutes=args.slot_minutes)
    params = SolveParams(gap=args.gap, time_limit_sec=args.time_limit,
                         solver=args.solver,
                         ring_switch_penalty_min=args.ring_switch_penalty,
                         forbid_ring_switches=args.forbid_ring_switches)

    print("Solving ...", file=sys.stderr)
    result = solve_show(show, params)
    print(result.summary(), file=sys.stderr)

    if result.status.upper() not in ("OPTIMAL", "FEASIBLE"):
        print(f"ERROR: No feasible solution (status={result.status}).",
              file=sys.stderr)
        sys.exit(1)

    out = args.output_file or args.show_file.replace(".xlsx", "_schedule.html")
    generate_chart(result, out)
    print(f"Written: {out}", file=sys.stderr)


if __name__ == "__main__":
    _cli()
