"""Two judging programs side by side, to compare and contrast: typically the published
program on the left and the optimizer's on the right.

1. A summary: finish, group start, hard-constraint violations and soft costs, in two columns
   with the difference.
2. The programs aligned **by judge** (default) or **by ring**. By judge is the useful view for
   a solver's program: its ring numbers are arbitrary labels, while each judge is in both.
3. Groups and Best in Show, and the validator's findings, side by side.
"""

from __future__ import annotations

import textwrap

from optdog.domain import Judge, JudgingProgram, Ring, fmt_time
from optdog.params import ShowParameters
from optdog.render.text import event_line, pinned_ids
from optdog.validate import ValidationResult, validate

Section = tuple[str, list[str]]


def _short_block(sb, p: ShowParameters) -> bool:
    return sb.block.entries < p.short_block_dogs


def _block_head(sb, pinned: set[int], ring: bool) -> str:
    where = f"{sb.ring}  " if ring else ""
    mark = "  pinned" if id(sb) in pinned else ""
    return (f"  {where}{fmt_time(sb.start)} – {fmt_time(int(round(sb.end)))}   "
            f"{sb.block.entries} dogs, {sb.block.duration:.0f} min{mark}")


def _judge_sections(program: JudgingProgram) -> dict[Judge, list[str]]:
    pinned = pinned_ids(program)
    out: dict[Judge, list[str]] = {}
    for judge, seq in program.by_judge().items():
        rings = []
        for sb in seq:
            if sb.ring not in rings:
                rings.append(sb.ring)
        lines = [f"{judge.name}  ({', '.join(str(r) for r in rings)}; {len(seq)} blocks; "
                 f"done {fmt_time(int(round(seq[-1].end)))})"]
        items = [(sb.start, 1, sb) for sb in seq]
        items += [(br.start, 0, br) for br in program.breaks if br.judge == judge]
        for _, kind, x in sorted(items, key=lambda t: (t[0], t[1])):
            if kind == 0:
                length = f"{x.minutes} min" if x.minutes else "length not stated"
                lines.append(f"  {fmt_time(x.start)}   LUNCH ({length})")
                continue
            lines.append(_block_head(x, pinned, ring=True))
            for e, off in x.block.offsets():
                lines.append(event_line(e, x.start + off, counts=False))
        out[judge] = lines
    return out


def _ring_sections(program: JudgingProgram) -> dict[Ring, list[str]]:
    pinned = pinned_ids(program)
    out: dict[Ring, list[str]] = {}
    for ring in sorted({sb.ring for sb in program.blocks}, key=lambda r: r.number):
        lines = [str(ring).upper()]
        judge = None
        for sb in program.ring_schedule(ring):
            if sb.judge != judge:
                judge = sb.judge
                lines.append(f"  {judge.name}")
            lines.append(_block_head(sb, pinned, ring=False))
            for e, off in sb.block.offsets():
                lines.append(event_line(e, sb.start + off, counts=False))
        out[ring] = lines
    return out


def _groups_section(program: JudgingProgram, res: ValidationResult) -> list[str]:
    show = program.show
    lines = ["GROUPS AND BEST IN SHOW"]
    if res.timeline is None:
        return lines + ["  (no group plan)"]
    for s in res.timeline.groups:
        wait = f"  waits {s.wait:.0f}" if s.wait > 0.5 else ""
        lines.append(f"  {fmt_time(int(round(s.start))):>8} – {fmt_time(int(round(s.end))):>8}  {s.group.value}{wait}")
    lines.append(f"  {fmt_time(int(round(res.timeline.bis_start))):>8} – "
                 f"{fmt_time(int(round(res.timeline.bis_end))):>8}  Best in Show")
    return lines


def _checks_section(res: ValidationResult) -> list[str]:
    lines = ["CHECKS"]
    lines += [f"  ✗ {v}" for v in res.violations] or ["  ✓ no hard-constraint violations"]
    return lines


# ----------------------------------------------------------------------------- summary


def _summary(left: tuple[str, JudgingProgram, ValidationResult],
             right: tuple[str, JudgingProgram, ValidationResult], p: ShowParameters) -> list[str]:
    def row(label, a, b, fmt=lambda x: f"{x:g}", diff=True):
        da = "–" if a is None else fmt(a)
        db = "–" if b is None else fmt(b)
        d = ""
        if diff and isinstance(a, (int, float)) and isinstance(b, (int, float)) and a != b:
            d = f"{b - a:+g}"
        return f"  {label:<34}{da:>22}{db:>22}{d:>10}"

    (la, pa, ra), (lb, pb, rb) = left, right

    def finish(r):
        return round(r.timeline.finish) if r.timeline else None

    def group_start(prog):
        return prog.groups.start if prog.groups and prog.groups.start is not None else None

    def first(prog):
        return min(sb.start for sb in prog.blocks) if prog.blocks else None

    def t(x):
        return fmt_time(int(x))

    def hard(r):
        return ", ".join(sorted({v.code for v in r.violations})) or "none"

    lines = [f"  {'':<34}{la[:21]:>22}{lb[:21]:>22}{'change':>10}",
             row("judging starts", first(pa), first(pb), t, diff=False),
             row("groups start", group_start(pa), group_start(pb), t),
             row("planned finish (BIS end)", finish(ra), finish(rb), t),
             f"  {'hard-constraint violations':<34}{hard(ra):>22}{hard(rb):>22}",
             row("blocks", len(pa.blocks), len(pb.blocks)),
             row(f"blocks under {p.short_block_dogs} dogs", sum(_short_block(sb, p) for sb in pa.blocks),
                 sum(_short_block(sb, p) for sb in pb.blocks))]
    names = {
        "S1_conflict_min": "S1 exhibitor-conflict minutes",
        "S2_ring_changes": "S2 judge ring changes",
        "S2_ring_changes_at_lunch": "S2 ... of them at lunch",
        "S3_equipment_switches": "S3 equipment switches",
        "S4_extra_group_switches": "S4 extra group switches",
        "S5_judge_idle_min": "S5 judge idle minutes",
        "S8_final_segment_excess_dogs": "S8 final-segment excess dogs",
    }
    for k, name in names.items():
        if k in ra.soft or k in rb.soft:
            lines.append(row(name, ra.soft.get(k), rb.soft.get(k)))
    if ra.objective is not None and rb.objective is not None:
        lines.append(row("weighted objective", round(ra.objective), round(rb.objective)))
    return lines


# ----------------------------------------------------------------------------- layout


def _wrap(lines: list[str], width: int) -> list[str]:
    out = []
    for line in lines:
        if len(line) <= width:
            out.append(line)
        else:
            indent = " " * (len(line) - len(line.lstrip()) + 4)
            out += textwrap.wrap(line, width, subsequent_indent=indent, drop_whitespace=False) or [""]
    return out


def _columns(a: list[str], b: list[str], width: int, gap: str = " │ ") -> list[str]:
    a, b = _wrap(a, width), _wrap(b, width)
    n = max(len(a), len(b))
    a += [""] * (n - len(a))
    b += [""] * (n - len(b))
    return [f"{x:<{width}}{gap}{y}".rstrip() for x, y in zip(a, b)]


def render_compare(left: tuple[str, JudgingProgram], right: tuple[str, JudgingProgram],
                   params: ShowParameters | None = None, by: str = "judge", width: int = 76,
                   title: str | None = None) -> str:
    """``left`` and ``right`` are (label, program) for the same show."""
    p = params or ShowParameters()
    (la, pa), (lb, pb) = left, right
    ra, rb = validate(pa, p), validate(pb, p)
    show = pa.show
    head = title or f"{show.name} — {show.date}"
    out = [head, "=" * len(head), "", "SUMMARY"]
    out += _summary((la, pa, ra), (lb, pb, rb), p)
    out += ["", "", *_columns([la, "-" * len(la)], [lb, "-" * len(lb)], width), ""]

    if by == "ring":
        sa, sb_ = _ring_sections(pa), _ring_sections(pb)
        keys = sorted(set(sa) | set(sb_), key=lambda r: r.number)
    else:
        sa, sb_ = _judge_sections(pa), _judge_sections(pb)
        first_seen = {j: min(s.start for s in seq) for j, seq in pa.by_judge().items()}
        first_seen.update({j: min(s.start for s in seq) for j, seq in pb.by_judge().items() if j not in first_seen})
        keys = sorted(set(sa) | set(sb_), key=lambda j: (first_seen[j], j.name))
    for k in keys:
        out += _columns(sa.get(k, ["(none)"]), sb_.get(k, ["(none)"]), width)
        out.append("")
    out += _columns(_groups_section(pa, ra), _groups_section(pb, rb), width)
    out.append("")
    out += _columns(_checks_section(ra), _checks_section(rb), width)
    return "\n".join(out) + "\n"
