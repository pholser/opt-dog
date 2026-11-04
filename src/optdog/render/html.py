"""A self-contained HTML ring/time chart of one or more JudgingPrograms.

Each program gets a summary, a chart with one row per ring (or per judge: toggle at the top),
blocks coloured by AKC group, lunches, pinned blocks, the groups and Best in Show in the group
ring(s), hard-constraint violations outlined, and the list of findings. Several programs (e.g.
the human program and the greedy schedule for the same show) share one time axis so they can
be compared row for row. No external resources: one file, works offline.
"""

from __future__ import annotations

import html
from collections import defaultdict

from optdog.domain import Group, JudgingProgram, Judge, Ring, ScheduledBlock, fmt_time
from optdog.params import ShowParameters
from optdog.render.text import pinned_ids
from optdog.validate import ValidationResult, _label, validate

PX_PER_MIN = 2.2
ROW_H = 46

_GROUP_KEY = {
    Group.SPORTING: "sporting", Group.HOUND: "hound", Group.WORKING: "working", Group.TERRIER: "terrier",
    Group.TOY: "toy", Group.NON_SPORTING: "nonsporting", Group.HERDING: "herding", Group.MISCELLANEOUS: "misc",
}
_BLOCK_CODES = {"H1", "H3", "H4"}  # violations whose subject is a block label
_JUDGE_CODES = {"H6", "V1", "H14"}  # subject is a judge name
_GROUP_CODES = {"H7", "H8", "H9", "H13", "H12"}


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _t(x: float) -> str:
    return fmt_time(int(round(x)))


def _block_group(sb: ScheduledBlock) -> str:
    weight: dict[str, int] = defaultdict(int)
    for e in sb.block.events:
        g = e.breed.group if e.breed is not None else None
        weight[_GROUP_KEY.get(g, "other")] += max(1, e.entries)
    return max(weight, key=weight.get)


def _surname(j: Judge) -> str:
    parts = [w for w in j.name.replace(",", " ").split() if not w.endswith(".") and w.upper() not in {"DVM", "JR", "SR"}]
    return parts[-1] if parts else j.name


def _block_conflicts(program: JudgingProgram, p: ShowParameters) -> dict[int, float]:
    if not program.show.entries:
        return {}
    from optdog.conflicts import exhibitor_conflicts

    block_of = {id(e): sb for e, sb, _ in program.scheduled_events()}
    out: dict[int, float] = defaultdict(float)
    for c in exhibitor_conflicts(program, p.exhibitor_travel_minutes).conflicts:
        for w in (c.a, c.b):
            out[id(block_of[id(w.event)])] += c.minutes
    return out


class _Chart:
    def __init__(self, t_min: int, t_max: int):
        self.t_min, self.t_max = t_min, t_max

    def x(self, t: float) -> float:
        return (t - self.t_min) * PX_PER_MIN

    @property
    def width(self) -> float:
        return self.x(self.t_max)

    def axis(self) -> str:
        ticks = []
        for t in range(self.t_min, self.t_max + 1, 60):
            first = ' first' if t == self.t_min else ''
            ticks.append(f'<div class="tick{first}" style="left:{self.x(t):.1f}px">{_e(fmt_time(t))}</div>')
        return f'<div class="axis" style="width:{self.width:.0f}px">{"".join(ticks)}</div>'

    def grid(self) -> str:
        lines = [f'<div class="gl{" hour" if (t - self.t_min) % 60 == 0 else ""}" style="left:{self.x(t):.1f}px"></div>'
                 for t in range(self.t_min, self.t_max + 1, 30)]
        return "".join(lines)


def _program_section(idx: int, label: str, program: JudgingProgram, res: ValidationResult,
                     chart: _Chart, p: ShowParameters) -> str:
    show = program.show
    plan = program.groups
    pinned = pinned_ids(program)
    conflicts = _block_conflicts(program, p)
    by_code: dict[str, list] = defaultdict(list)
    for v in res.violations:
        by_code[v.code].append(v)
    bad_blocks: dict[str, list[str]] = defaultdict(list)
    bad_judges: dict[str, list[str]] = defaultdict(list)
    for v in res.violations:
        if v.code in _BLOCK_CODES:
            bad_blocks[v.subject].append(f"{v.code}: {v.message}")
        elif v.code in _JUDGE_CODES:
            bad_judges[v.subject].append(f"{v.code}: {v.message}")
    group_bad = [v for v in res.violations if v.code in _GROUP_CODES]

    def block_div(sb: ScheduledBlock) -> str:
        grp = _block_group(sb)
        lbl = _label(sb)
        cls = ["blk", f"g-{grp}"]
        if id(sb) in pinned:
            cls.append("pinned")
        if lbl in bad_blocks:
            cls.append("bad")
        names = [f"{e.entries} {e.name}" for e in sb.block.events]
        tip = [f"{sb.judge.name}", f"{sb.ring} · {_t(sb.start)}–{_t(sb.end)} · {sb.block.entries} dogs, {sb.block.duration:.0f} min"]
        if id(sb) in pinned:
            tip.append(f"pinned ({sb.block.events[0].competition.value})")
        tip += [""] + [f"{_t(sb.start + off)}  {e.entries:>3} {e.name}" for e, off in sb.block.offsets()]
        cm = conflicts.get(id(sb), 0.0)
        if cm:
            tip.append(f"\nexhibitor conflicts touching this block: {cm:.0f} min")
        tip += [""] + bad_blocks.get(lbl, [])
        short = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"
        warn = '<span class="warn">!</span>' if lbl in bad_blocks else ""
        conf = f'<span class="conf">{cm:.0f}′</span>' if cm >= 1 and sb.block.duration * PX_PER_MIN >= 70 else ""
        return (f'<div class="{" ".join(cls)}" style="left:{chart.x(sb.start):.1f}px;width:{max(2.0, sb.block.duration * PX_PER_MIN):.1f}px" '
                f'data-tip="{_e(chr(10).join(tip).strip())}">{warn}<b>{_e(_surname(sb.judge))}</b> '
                f'<span class="sub">{_e(short)}</span>{conf}</div>')

    def lunch_div(br) -> str:
        if br.minutes is None:
            return ""
        tip = f"{br.judge.name}\nlunch {_t(br.start)}–{_t(br.end)} ({br.minutes} min)"
        return (f'<div class="lunch" style="left:{chart.x(br.start):.1f}px;width:{br.minutes * PX_PER_MIN:.1f}px" '
                f'data-tip="{_e(tip)}">lunch</div>')

    def groups_divs(judge: Judge | None = None) -> str:
        tl = res.timeline
        if tl is None:
            return ""
        out = []
        for s in tl.groups:
            gj = show.group_judges.get(s.group)
            if judge is not None and gj != judge:
                continue
            if s.end - s.start <= 0:
                continue
            tip = (f"{s.group.value} Group · {_t(s.start)}–{_t(s.end)}\n{show.group_entrants(s.group)} entrants"
                   f"\njudge: {gj.name if gj else '?'}\nbreeds done {_t(s.breeds_done)}, ready {_t(s.ready)}"
                   + (f"\nwaited {s.wait:.0f} min" if s.wait > 0.5 else ""))
            out.append(f'<div class="grp g-{_GROUP_KEY[s.group]}" style="left:{chart.x(s.start):.1f}px;'
                       f'width:{(s.end - s.start) * PX_PER_MIN:.1f}px" data-tip="{_e(tip)}">{_e(s.group.value)}</div>')
        if judge is None or judge == show.bis_judge:
            tip = f"Best in Show · {_t(tl.bis_start)}–{_t(tl.bis_end)}\n{show.bis_entrants()} entrants\njudge: {show.bis_judge.name if show.bis_judge else '?'}"
            out.append(f'<div class="grp bis" style="left:{chart.x(tl.bis_start):.1f}px;'
                       f'width:{(tl.bis_end - tl.bis_start) * PX_PER_MIN:.1f}px" data-tip="{_e(tip)}">BIS</div>')
        return "".join(out)

    marker = ""
    if plan and plan.start is not None:
        marker = (f'<div class="mark" style="left:{chart.x(plan.start):.1f}px" '
                  f'data-tip="{_e("published group start " + fmt_time(plan.start))}"></div>')

    def row(label_html: str, content: str, cls: str = "", tip: str = "") -> str:
        t = f' data-tip="{_e(tip)}"' if tip else ""
        return (f'<div class="row {cls}"><div class="rl"{t}>{label_html}</div>'
                f'<div class="lane" style="width:{chart.width:.0f}px">{chart.grid()}{marker}{content}</div></div>')

    group_rings = set(plan.rings) if plan else set(show.group_rings)
    rings: list[Ring] = sorted({sb.ring for sb in program.blocks} | group_rings, key=lambda r: r.number)
    ring_rows = []
    for ring in rings:
        content = "".join(block_div(sb) for sb in program.ring_schedule(ring))
        content += "".join(lunch_div(br) for br in program.breaks if br.ring == ring)
        is_g = ring in group_rings
        if is_g and ring == min(group_rings, key=lambda r: r.number):
            content += groups_divs()
        ring_rows.append(row(f'{_e(ring)}{" <span class=gr>groups</span>" if is_g else ""}', content,
                             "grow" if is_g else ""))

    judges = sorted({sb.judge for sb in program.blocks} | set(show.group_judges.values())
                    | ({show.bis_judge} if show.bis_judge else set()),
                    key=lambda j: (min((sb.start for sb in program.judge_schedule(j)), default=10_000), j.name))
    judge_rows = []
    for j in judges:
        content = "".join(block_div(sb) for sb in program.judge_schedule(j))
        content += "".join(lunch_div(br) for br in program.breaks if br.judge == j)
        content += groups_divs(j)
        roles = [g.value for g, gj in show.group_judges.items() if gj == j]
        if j == show.bis_judge:
            roles.append("BIS")
        load = show.judge_load(j)
        tip = f"{j.name}\n{load} entries" + (f"\njudges: {', '.join(roles)}" if roles else "")
        tip += "".join("\n" + m for m in bad_judges.get(j.name, []))
        warn = '<span class="warn">!</span>' if j.name in bad_judges else ""
        lbl = f'{warn}{_e(_surname(j))}{" <span class=gr>" + _e("/".join(r[:4] for r in roles)) + "</span>" if roles else ""}'
        judge_rows.append(row(lbl, content, "", tip))

    m = res.metrics
    s1 = res.soft.get("S1_conflict_min")
    stats = [
        ("groups", m.get("groups_start") or "–"),
        ("finish", m.get("finish") or "–"),
        ("hard violations", str(len(res.violations))),
        ("conflict min", f"{s1:.0f}" if s1 is not None else "–"),
        ("ring changes", str(res.soft.get("S2_ring_changes", 0) + res.soft.get("S2_ring_changes_at_lunch", 0))),
        ("objective", f"{res.objective:.0f}" if res.objective is not None else "–"),
    ]
    stat_html = "".join(f'<div class="stat"><div class="k">{_e(k)}</div><div class="v">{_e(v)}</div></div>' for k, v in stats)
    order = " → ".join(s.group.value for s in res.timeline.groups) if res.timeline else ""
    if res.violations:
        items = "".join(f"<li><b>{_e(v.code)}</b> {_e(v.subject)}: {_e(v.message)}</li>" for v in res.violations)
        findings = f'<details class="find"><summary>{len(res.violations)} hard-constraint violation(s)</summary><ul>{items}</ul></details>'
    else:
        findings = '<p class="ok">No hard-constraint violations.</p>'
    soft = " · ".join(f"{_e(k)} {v:g}" for k, v in res.soft.items())
    gb = "".join(f" <span class=badtag>{_e(v.code)}</span>" for v in group_bad)

    return f"""
<section class="prog">
  <h2>{_e(label)}</h2>
  <div class="stats">{stat_html}</div>
  <p class="order">Group order{gb}: {_e(order)}</p>
  <div class="scroller">
    <div class="chart">
      <div class="row axisrow"><div class="rl"></div>{chart.axis()}</div>
      <div class="view-rings">{"".join(ring_rows)}</div>
      <div class="view-judges">{"".join(judge_rows)}</div>
    </div>
  </div>
  {findings}
  <p class="soft">{soft}</p>
</section>"""


_CSS = """
:root{--bg:#fbfaf7;--panel:#ffffff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e4e2dc;--hour:#d3d0c8;--bad:#d1242f;
--sporting:#2f6fd0;--hound:#b8781f;--working:#c23b2f;--terrier:#7350c4;--toy:#cc4b93;--nonsporting:#16917f;
--herding:#4f8a1f;--misc:#80808a;--other:#9a7051;--lunch:#9a9aa3;--mark:#111}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141416;--panel:#1c1c1f;--fg:#ececef;
--muted:#9b9ba3;--line:#2b2b30;--hour:#3a3a40;--bad:#ff5c5c;--sporting:#6ea2ff;--hound:#e3a64a;--working:#f0705f;
--terrier:#a88bff;--toy:#f07fbf;--nonsporting:#3fc9b2;--herding:#8cc659;--misc:#a7a7b0;--other:#c79a78;--lunch:#77777f;--mark:#eee}}
:root[data-theme="dark"]{--bg:#141416;--panel:#1c1c1f;--fg:#ececef;--muted:#9b9ba3;--line:#2b2b30;--hour:#3a3a40;--bad:#ff5c5c;
--sporting:#6ea2ff;--hound:#e3a64a;--working:#f0705f;--terrier:#a88bff;--toy:#f07fbf;--nonsporting:#3fc9b2;--herding:#8cc659;
--misc:#a7a7b0;--other:#c79a78;--lunch:#77777f;--mark:#eee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:100%;padding:16px}
h1{font-size:20px;margin:0 0 4px} h2{font-size:16px;margin:24px 0 8px}
.meta,.order,.soft{color:var(--muted);margin:4px 0}
.soft{font-size:12px}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
.controls button{font:inherit;padding:4px 12px;border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:6px;cursor:pointer}
.controls button[aria-pressed="true"]{border-color:var(--fg);font-weight:600}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:4px}
.legend i{width:12px;height:12px;border-radius:3px;display:inline-block;background:color-mix(in srgb,var(--c) 30%,var(--panel));border-left:3px solid var(--c)}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 10px;min-width:88px}
.stat .k{font-size:11px;color:var(--muted)} .stat .v{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.scroller{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel);margin-top:8px}
.chart{display:inline-block;min-width:100%}
.row{display:flex;height:__ROW__px;border-bottom:1px solid var(--line)}
.row.axisrow{height:24px}
.rl{position:sticky;left:0;z-index:3;width:150px;flex:none;background:var(--panel);border-right:1px solid var(--line);
padding:0 8px;display:flex;align-items:center;gap:4px;font-size:12px;white-space:nowrap;overflow:hidden}
.row.grow .rl{font-weight:600}
.gr{font-size:10px;color:var(--muted);font-weight:400}
.lane{position:relative;flex:none;height:100%}
.axis{position:relative;height:24px}
.tick{position:absolute;top:4px;transform:translateX(-50%);font-size:11px;color:var(--muted);white-space:nowrap}
.tick.first{transform:translateX(2px)}
.gl{position:absolute;top:0;bottom:0;width:0;border-left:1px dashed var(--line)}
.gl.hour{border-left:1px solid var(--hour)}
.mark{position:absolute;top:0;bottom:0;width:0;border-left:2px dotted var(--mark);z-index:2}
.blk,.grp,.lunch{position:absolute;top:5px;bottom:5px;border-radius:4px;overflow:hidden;white-space:nowrap;
font-size:11px;padding:2px 4px;line-height:1.25;cursor:default}
.blk{background:color-mix(in srgb,var(--c) 22%,var(--panel));border-left:3px solid var(--c)}
.blk .sub{display:block;color:var(--muted);text-overflow:ellipsis;overflow:hidden}
.blk.pinned{background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--c) 22%,var(--panel)) 0 6px,var(--panel) 6px 10px)}
.blk.bad{outline:2px solid var(--bad);outline-offset:-2px}
.warn{color:var(--bad);font-weight:700;margin-right:3px}
.badtag{color:var(--bad);font-weight:700}
.conf{position:absolute;right:3px;top:2px;font-size:10px;color:var(--muted)}
.grp{background:var(--c);color:#fff;font-weight:600;text-align:center;top:8px;bottom:8px}
.grp.bis{--c:var(--fg);background:var(--fg);color:var(--bg)}
.lunch{top:14px;bottom:14px;border:1px dashed var(--lunch);color:var(--muted);font-size:10px;background:transparent}
.g-sporting{--c:var(--sporting)}.g-hound{--c:var(--hound)}.g-working{--c:var(--working)}.g-terrier{--c:var(--terrier)}
.g-toy{--c:var(--toy)}.g-nonsporting{--c:var(--nonsporting)}.g-herding{--c:var(--herding)}.g-misc{--c:var(--misc)}.g-other{--c:var(--other)}
body.judges .view-rings{display:none} body:not(.judges) .view-judges{display:none}
.find{margin-top:8px} .find ul{margin:6px 0;padding-left:20px;font-size:13px}
.find summary{cursor:pointer;color:var(--bad);font-weight:600}
.ok{color:var(--muted)}
#tip{position:fixed;z-index:10;pointer-events:none;background:var(--panel);color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:6px 8px;font:12px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre;
box-shadow:0 4px 16px rgba(0,0,0,.18);display:none;max-width:90vw;overflow:hidden}
""".replace("__ROW__", str(ROW_H))

_JS = """
const tip=document.getElementById('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest('[data-tip]');
 if(!t){tip.style.display='none';return}
 tip.textContent=t.dataset.tip;tip.style.display='block';
 const w=tip.offsetWidth,h=tip.offsetHeight;let x=e.clientX+14,y=e.clientY+14;
 if(x+w>innerWidth-8)x=Math.max(8,e.clientX-w-14); if(y+h>innerHeight-8)y=Math.max(8,e.clientY-h-14);
 tip.style.left=x+'px';tip.style.top=y+'px';});
const btns=document.querySelectorAll('[data-view]');
function setView(v){document.body.classList.toggle('judges',v==='judges');
 btns.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===v)));
 try{localStorage.setItem('optdog-view',v)}catch(e){}}
btns.forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
let v='rings';try{v=localStorage.getItem('optdog-view')||'rings'}catch(e){}
setView(v);
"""


def render_html(programs: list[tuple[str, JudgingProgram]], params: ShowParameters | None = None,
                title: str | None = None) -> str:
    """One page charting ``programs`` (label, program) on a shared time axis."""
    p = params or ShowParameters()
    results = [(label, prog, validate(prog, p)) for label, prog in programs]
    starts = [sb.start for _, prog, _ in results for sb in prog.blocks]
    ends = [r.timeline.finish if r.timeline else max(sb.end for sb in prog.blocks) for _, prog, r in results]
    t_min = (min(starts) // 60) * 60
    t_max = int(-(-max(ends) // 60) * 60)
    chart = _Chart(int(t_min), t_max)
    show = programs[0][1].show
    head = title or f"{show.name}"
    sections = "".join(_program_section(i, label, prog, res, chart, p) for i, (label, prog, res) in enumerate(results))
    legend = "".join(f'<span class="g-{k}"><i></i>{_e(g.value)}</span>' for g, k in _GROUP_KEY.items())
    legend += '<span class="g-other"><i></i>Juniors / other</span>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(head)}</title><style>{_CSS}</style></head>
<body><main>
<h1>{_e(head)}</h1>
<p class="meta">{_e(show.date)} · planned at the judges' rates (25 dogs/hr) · groups and BIS at upper-bound durations</p>
<div class="controls">
  <button data-view="rings" aria-pressed="true">By ring</button>
  <button data-view="judges" aria-pressed="false">By judge</button>
  <div class="legend">{legend}<span>▨ pinned</span><span style="color:var(--bad)">▢ violation</span><span>⋮ published group start</span></div>
</div>
{sections}
</main><div id="tip"></div><script>{_JS}</script></body></html>
"""
