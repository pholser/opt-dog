"""Turn a parsed (human-made) judging program into a scheduling instance.

The instance keeps what the program's author was *given* and drops what they *decided*:

- **Kept as input:** judges, breed events and their dogs, group and BIS judges, group rings,
  judging start, and the rings in use.
- **Re-planned:** every event: how they are blocked, where and when, and the group order.

Parsed programs hold only regular breed events of the all-breed show (U 2026-11-13), so nothing
is pinned; a problem's own pinned blocks (SPEC H11) are kept.
"""

from __future__ import annotations

from dataclasses import replace

from optdog.domain import JudgingProgram, Show


def instance_from_program(program: JudgingProgram) -> Show:
    """The scheduling problem behind ``program``."""
    show = program.show
    rings = sorted({sb.ring for sb in program.blocks} | set(show.group_rings), key=lambda r: r.number)
    judges = [j for j in show.judges if any(e.judge == j for e in show.events)]
    start = show.start if show.start is not None else min(sb.start for sb in program.blocks)
    return replace(show, judges=judges, rings=rings, start=start, events=list(show.events),
                   entries=list(show.entries), pinned=list(show.pinned))
