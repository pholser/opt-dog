"""A plain-text judging program, laid out like a published one: by ring, judge and time,
then the groups and Best in Show, then the validator's findings."""

from __future__ import annotations

from optdog.domain import EntryCounts, JudgingEvent, JudgingProgram, fmt_time
from optdog.instance import PINNED_COMPETITIONS
from optdog.params import ShowParameters
from optdog.validate import ValidationResult, validate


def pinned_ids(program: JudgingProgram) -> set[int]:
    """Blocks a scheduler must keep: the show's pinned blocks, or (for a parsed program, whose
    Show has none) every special-attraction block, which an instance would pin (SPEC H11)."""
    ids = {id(sb) for sb in program.show.pinned}
    ids |= {id(sb) for sb in program.blocks if sb.block.events[0].competition in PINNED_COMPETITIONS}
    return ids


def _counts(c: EntryCounts | None) -> str:
    if c is None:
        return ""
    s = f"{c.class_dogs}-{c.class_bitches}-({c.bob_dogs}-{c.bob_bitches})"
    return s + (f"-{c.nonregular}" if c.nonregular else "")


def _event_line(e: JudgingEvent, start: float) -> str:
    tags = []
    if e.breed is not None and e.breed.examination.needs_table:
        tags.append("table")
    elif e.breed is not None and e.breed.examination.needs_ramp:
        tags.append("ramp")
    tag = f"  [{', '.join(tags)}]" if tags else ""
    return f"      {fmt_time(int(round(start))):>8}  {e.entries:>3}  {e.name:<38} {_counts(e.counts):<14}{tag}".rstrip()


def render_text(program: JudgingProgram, params: ShowParameters | None = None,
                result: ValidationResult | None = None, title: str | None = None) -> str:
    p = params or ShowParameters()
    res = result or validate(program, p)
    show = program.show
    plan = program.groups
    out: list[str] = []
    head = title or f"{show.name} — {show.date}"
    out += [head, "=" * len(head)]
    info = [f"judging from {fmt_time(show.start)}" if show.start is not None else ""]
    if plan and plan.start is not None:
        info.append(f"groups {fmt_time(plan.start)} in {', '.join(str(r) for r in plan.rings)}")
    if res.timeline:
        info.append(f"planned finish {fmt_time(int(round(res.timeline.finish)))}")
    out += ["   ".join(x for x in info if x), ""]

    pinned = pinned_ids(program)
    # print each break under the judge's last block that starts before it
    after: dict[int, list] = {}
    for br in sorted(program.breaks, key=lambda b: b.start):
        before = [sb for sb in program.judge_schedule(br.judge) if sb.start <= br.start]
        if before:
            after.setdefault(id(before[-1]), []).append(br)
    rings = sorted({sb.ring for sb in program.blocks}, key=lambda r: r.number)
    for ring in rings:
        out.append(f"{str(ring).upper()}")
        judge = None
        for sb in program.ring_schedule(ring):
            if sb.judge != judge:
                judge = sb.judge
                out.append(f"  {judge.name}")
            mark = f"  (pinned: {sb.block.events[0].competition.value})" if id(sb) in pinned else ""
            out.append(f"    {fmt_time(sb.start)} – {fmt_time(int(round(sb.end)))}   "
                       f"{sb.block.entries} dogs, {sb.block.duration:.0f} min{mark}")
            for e, off in sb.block.offsets():
                out.append(_event_line(e, sb.start + off))
            for br in after.get(id(sb), []):
                length = f"{br.minutes} min" if br.minutes else "length not stated"
                out.append(f"    {fmt_time(br.start)}   LUNCH ({length})")
        out.append("")

    if res.timeline:
        out.append("GROUPS AND BEST IN SHOW")
        for s in res.timeline.groups:
            judge = show.group_judges.get(s.group)
            wait = f"   waits {s.wait:.0f} min" if s.wait > 0.5 else ""
            out.append(f"  {fmt_time(int(round(s.start))):>8} – {fmt_time(int(round(s.end))):>8}  {s.group.value:<13} "
                       f"{show.group_entrants(s.group):>2} entrants  {judge.name if judge else ''}{wait}")
        bis = show.bis_judge.name if show.bis_judge else ""
        out.append(f"  {fmt_time(int(round(res.timeline.bis_start))):>8} – {fmt_time(int(round(res.timeline.bis_end))):>8}  "
                   f"{'Best in Show':<13} {show.bis_entrants():>2} entrants  {bis}")
        out.append("")

    out.append("CHECKS")
    if res.violations:
        for v in res.violations:
            out.append(f"  ✗ {v}")
    else:
        out.append("  ✓ no hard-constraint violations")
    soft = ", ".join(f"{k} {v:g}" for k, v in res.soft.items())
    out.append(f"  soft: {soft}")
    if res.objective is not None:
        out.append(f"  objective: {res.objective:.0f}")
    return "\n".join(out) + "\n"
