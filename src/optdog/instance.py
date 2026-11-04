"""Turn a parsed (human-made) judging program into a scheduling instance.

The instance keeps what the program's author was *given* and drops what they *decided*:

- **Kept as input:** judges, all conformation events and their entries, group and BIS
  judges, group rings, judging start, and the conformation rings in use.
- **Pinned (SPEC H11):** blocks of concurrent specialties, Beginner Puppy and FSS open
  shows. Clubs publish these times in the premium list, and their events chain
  (puppy breeds → puppy groups → Best Puppy in Show; FSS show #1 → #2 with the same
  dogs), so they stay at their published ring and time.
- **Re-planned:** every all-breed event (breeds, varieties, sweepstakes, Junior
  Showmanship): how they are blocked, where and when, and the group order.
- **Dropped:** obedience and rally, which run in their own rings.
"""

from __future__ import annotations

from dataclasses import replace

from optdog.domain import Competition, JudgingProgram, ScheduledBlock, Show

CONFORMATION = {Competition.ALL_BREED, Competition.SPECIALTY, Competition.BEGINNER_PUPPY, Competition.FSS_OPEN}
PINNED_COMPETITIONS = {Competition.SPECIALTY, Competition.BEGINNER_PUPPY, Competition.FSS_OPEN}


def _competition(sb: ScheduledBlock) -> Competition:
    return sb.block.events[0].competition


def instance_from_program(program: JudgingProgram) -> Show:
    """The scheduling problem behind ``program``: a Show with pinned special-attraction blocks."""
    show = program.show
    conf_blocks = [sb for sb in program.blocks if _competition(sb) in CONFORMATION]
    pinned = [sb for sb in conf_blocks if _competition(sb) in PINNED_COMPETITIONS]
    events = [e for sb in conf_blocks for e in sb.block.events]
    rings = sorted({sb.ring for sb in conf_blocks} | set(show.group_rings), key=lambda r: r.number)
    judges = [j for j in show.judges if any(e.judge == j for e in events)]
    start = show.start if show.start is not None else min(sb.start for sb in conf_blocks)
    ids = {id(e) for e in events}
    entries = [en for en in show.entries if id(en.event) in ids]
    return replace(show, judges=judges, events=events, rings=rings, start=start, pinned=pinned, entries=entries)
