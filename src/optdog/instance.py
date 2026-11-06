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
- **Dropped:** obedience and rally, which run in their own rings, except that the blocks of a
  judge who also judges conformation are pinned: they are that judge's commitments, whose
  rings stay out of the show's rings, so no conformation is placed there.
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
    conf_judges = {sb.judge for sb in conf_blocks}
    commitments = [sb for sb in program.blocks if _competition(sb) not in CONFORMATION and sb.judge in conf_judges]
    pinned = [sb for sb in conf_blocks if _competition(sb) in PINNED_COMPETITIONS] + commitments
    events = [e for sb in conf_blocks + commitments for e in sb.block.events]
    rings = sorted({sb.ring for sb in conf_blocks} | set(show.group_rings), key=lambda r: r.number)
    judges = [j for j in show.judges if any(e.judge == j for e in events)]
    start = show.start if show.start is not None else min(sb.start for sb in conf_blocks)
    ids = {id(e) for e in events}
    entries = [en for en in show.entries if id(en.event) in ids]
    return replace(show, judges=judges, events=events, rings=rings, start=start, pinned=pinned, entries=entries)


def is_conformation(sb: ScheduledBlock) -> bool:
    return _competition(sb) in CONFORMATION


def conformation_only(program: JudgingProgram) -> JudgingProgram:
    """``program`` without obedience and rally, to compare a published program with a generated one."""
    blocks = [sb for sb in program.blocks if is_conformation(sb)]
    show = program.show
    judges = {sb.judge for sb in blocks}
    ids = {id(e) for sb in blocks for e in sb.block.events}
    kept = {id(sb) for sb in blocks}
    view = replace(show, judges=[j for j in show.judges if j in judges],
                   events=[e for sb in blocks for e in sb.block.events],
                   pinned=[sb for sb in show.pinned if id(sb) in kept],
                   entries=[en for en in show.entries if id(en.event) in ids])
    return JudgingProgram(view, blocks, [br for br in program.breaks if br.judge in judges], program.groups)
