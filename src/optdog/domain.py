"""Domain model for AKC all-breed conformation show scheduling.

Vocabulary (see docs/SPEC.md):

- A **Show** is one day's all-breed conformation event: judges, the judging events
  they are assigned, rings, and group/BIS assignments. It is the problem *instance*.
- A **JudgingEvent** is the smallest thing judged without interruption: a breed or
  variety, a sweepstakes, Junior Showmanship, etc.
- A **Block** is an ordered run of one judge's events published under a single
  "not before" time.
- A **JudgingProgram** places blocks in rings at start times, places judges' breaks,
  and fixes the group order. It is a *solution* (or a parsed human-made schedule).

Times are minutes after midnight (``Minutes``). This module has no I/O or solver
dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator

Minutes = int  # minutes after midnight


def fmt_time(t: Minutes) -> str:
    h, m = divmod(t, 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


# --------------------------------------------------------------------------- breeds


class Group(Enum):
    """AKC breed classification.

    The seven *variety groups* are judged as groups (and feed Best in Show).
    MISCELLANEOUS is AKC's Miscellaneous Class: recognized breeds that compete in
    the breed ring and for Best in Miscellaneous, but have no group judging and do
    not advance to Best in Show. See ``is_variety_group`` / ``VARIETY_GROUPS``.
    """

    SPORTING = "Sporting"
    HOUND = "Hound"
    WORKING = "Working"
    TERRIER = "Terrier"
    TOY = "Toy"
    NON_SPORTING = "Non-Sporting"
    HERDING = "Herding"
    MISCELLANEOUS = "Miscellaneous"

    @property
    def is_variety_group(self) -> bool:
        """True for the seven groups that are judged as groups before Best in Show."""
        return self is not Group.MISCELLANEOUS


VARIETY_GROUPS: tuple[Group, ...] = tuple(g for g in Group if g.is_variety_group)


class Examination(Enum):
    """Where dogs of a breed are examined (AKC table/ramp breeds list)."""

    GROUND = "ground"
    TABLE = "table"
    RAMP = "ramp"  # ramp mandatory
    GROUND_OR_RAMP = "ground_or_ramp"  # ramp optional, judge's discretion
    TABLE_OR_RAMP = "table_or_ramp"
    ANY = "any"  # Miniature Bull Terrier: ground, table or ramp

    @property
    def needs_table(self) -> bool:
        return self is Examination.TABLE

    @property
    def needs_ramp(self) -> bool:
        return self is Examination.RAMP


@dataclass(frozen=True)
class Breed:
    """An AKC breed or variety (the unit that is judged for Best of Breed/Variety).

    ``group`` is one of the seven variety groups, ``Group.MISCELLANEOUS`` for
    Miscellaneous Class breeds, or None for Foundation Stock Service (FSS) breeds,
    which AKC has not yet admitted to the Miscellaneous Class.
    """

    name: str
    group: Group | None
    examination: Examination = Examination.GROUND
    variety_of: str | None = None

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------- entries


@dataclass(frozen=True)
class EntryCounts:
    """Entry breakdown as printed in judging programs: ``dogs-bitches-(BOB dogs-BOB bitches)``
    plus non-regular classes (veterans, etc.).

    Breed judging order: regular class dogs, regular class bitches, non-regular
    classes, then Best of Breed (champions plus Winners Dog and Winners Bitch).
    """

    class_dogs: int = 0
    class_bitches: int = 0
    bob_dogs: int = 0
    bob_bitches: int = 0
    nonregular: int = 0

    @property
    def total(self) -> int:
        return self.class_dogs + self.class_bitches + self.bob_dogs + self.bob_bitches + self.nonregular

    @property
    def before_bob(self) -> int:
        """Dogs judged before the Best of Breed competition starts."""
        return self.class_dogs + self.class_bitches + self.nonregular

    @property
    def champions(self) -> int:
        return self.bob_dogs + self.bob_bitches


# --------------------------------------------------------------------------- judges


DEFAULT_MINUTES_PER_DOG = 60 / 25  # Rules Ch.7 §12: about 25 dogs per hour
PERMIT_MINUTES_PER_DOG = 60 / 20  # Show Manual §6: permit/sweeps judges about 20/hr


@dataclass(frozen=True)
class Judge:
    name: str
    permit: bool = False
    minutes_per_dog: float | None = None  # per-judge override

    @property
    def rate(self) -> float:
        """Minutes per dog."""
        if self.minutes_per_dog is not None:
            return self.minutes_per_dog
        return PERMIT_MINUTES_PER_DOG if self.permit else DEFAULT_MINUTES_PER_DOG

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------- events


class Competition(Enum):
    """Which competition an event belongs to; most of a day is the all-breed show,
    but programs interleave special attractions and concurrent events."""

    ALL_BREED = "all-breed"
    SPECIALTY = "specialty"  # concurrent or designated specialty
    BEGINNER_PUPPY = "beginner-puppy"
    FSS_OPEN = "fss-open"
    OBEDIENCE = "obedience"
    RALLY = "rally"
    OTHER = "other"


class EventKind(Enum):
    BREED = "breed"
    SWEEPSTAKES = "sweepstakes"
    JUNIOR_SHOWMANSHIP = "junior-showmanship"
    GROUP = "group"  # e.g. Beginner Puppy groups judged in a breed ring
    OTHER = "other"  # obedience classes, Best in Misc, Pee Wee, ...


@dataclass(frozen=True)
class JudgingEvent:
    """Something judged without interruption by one judge in one ring."""

    name: str
    kind: EventKind
    judge: Judge
    entries: int
    breed: Breed | None = None
    counts: EntryCounts | None = None
    competition: Competition = Competition.ALL_BREED
    minutes_per_entry: float | None = None  # overrides the judge's rate (e.g. juniors)

    @property
    def rate(self) -> float:
        if self.minutes_per_entry is not None:
            return self.minutes_per_entry
        if self.kind is EventKind.JUNIOR_SHOWMANSHIP:
            return 60 / 20  # Show Manual §6: juniors at 20 per hour
        return self.judge.rate

    @property
    def duration(self) -> float:
        """Planned minutes to judge this event."""
        return self.entries * self.rate

    @property
    def bob_offset(self) -> float:
        """Minutes from the event's start until Best of Breed (champions) begins."""
        if self.counts is None:
            return 0.0
        return self.counts.before_bob * self.rate

    @property
    def group(self) -> Group | None:
        """The breed's AKC group (including MISCELLANEOUS) for breed events; else None."""
        return self.breed.group if self.breed and self.kind is EventKind.BREED else None

    @property
    def feeds_group(self) -> Group | None:
        """The variety group this event must finish before, or None (Rules: breeds before their group)."""
        g = self.group
        return g if g is not None and g.is_variety_group else None

    def __str__(self) -> str:
        return f"{self.entries} {self.name}"


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class Block:
    """An ordered run of one judge's events published under one 'not before' time."""

    judge: Judge
    events: tuple[JudgingEvent, ...]

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("a block needs at least one event")
        others = {e.judge for e in self.events} - {self.judge}
        if others:
            raise ValueError(f"block for {self.judge} contains events judged by {others}")

    @property
    def entries(self) -> int:
        return sum(e.entries for e in self.events)

    @property
    def duration(self) -> float:
        return sum(e.duration for e in self.events)

    def offsets(self) -> Iterator[tuple[JudgingEvent, float]]:
        """Each event with its planned start offset (minutes) from the block start."""
        t = 0.0
        for e in self.events:
            yield e, t
            t += e.duration


@dataclass(frozen=True)
class Ring:
    number: int

    def __str__(self) -> str:
        return f"Ring {self.number}"


@dataclass(frozen=True)
class ScheduledBlock:
    block: Block
    ring: Ring
    start: Minutes  # published "not before" time

    @property
    def judge(self) -> Judge:
        return self.block.judge

    @property
    def end(self) -> float:
        return self.start + self.block.duration


@dataclass(frozen=True)
class Break:
    """A judge's meal/rest break (Rules Ch.7 §12: 45–60 min if assignment > 5 hours)."""

    judge: Judge
    ring: Ring | None
    start: Minutes
    minutes: int | None = None  # None when the program does not state the length

    @property
    def end(self) -> float | None:
        return None if self.minutes is None else self.start + self.minutes


# --------------------------------------------------------------------------- groups


@dataclass(frozen=True)
class GroupsPlan:
    """Group judging and Best in Show, run sequentially in the group ring(s).

    ``order`` contains the seven variety groups only (``VARIETY_GROUPS``); the
    Miscellaneous Class has no group judging.
    """

    rings: tuple[Ring, ...]
    start: Minutes | None
    order: tuple[Group, ...]
    judges: dict[Group, Judge] = field(default_factory=dict, hash=False, compare=False)
    bis_judge: Judge | None = None


# --------------------------------------------------------------------------- show / program


@dataclass
class Show:
    """Problem instance: one day's all-breed show."""

    name: str
    date: str  # ISO date or day label as printed
    judges: list[Judge]
    events: list[JudgingEvent]
    rings: list[Ring]
    group_judges: dict[Group, Judge] = field(default_factory=dict)
    bis_judge: Judge | None = None
    group_rings: tuple[Ring, ...] = ()
    start: Minutes | None = None

    def events_for(self, judge: Judge) -> list[JudgingEvent]:
        return [e for e in self.events if e.judge == judge]

    def judge_load(self, judge: Judge) -> int:
        """Entries counting toward the AKC 175/day limit (Rules Ch.7 §13)."""
        return sum(e.entries for e in self.events_for(judge) if e.competition in _COUNTS_TOWARD_LIMIT)

    def breeds_in(self, group: Group) -> list[JudgingEvent]:
        """Breed events of an AKC group (MISCELLANEOUS included)."""
        return [e for e in self.events if e.group is group]

    def feeding(self, group: Group) -> list[JudgingEvent]:
        """Events that must finish before ``group`` is judged (empty for MISCELLANEOUS)."""
        return [e for e in self.events if e.feeds_group is group]


_COUNTS_TOWARD_LIMIT = {
    Competition.ALL_BREED,
    Competition.SPECIALTY,
    Competition.BEGINNER_PUPPY,
    Competition.FSS_OPEN,
    Competition.OBEDIENCE,
    Competition.RALLY,
}


@dataclass
class JudgingProgram:
    """A schedule: blocks placed in rings and times, breaks, and the group plan."""

    show: Show
    blocks: list[ScheduledBlock]
    breaks: list[Break] = field(default_factory=list)
    groups: GroupsPlan | None = None

    def ring_schedule(self, ring: Ring) -> list[ScheduledBlock]:
        return sorted((b for b in self.blocks if b.ring == ring), key=lambda b: b.start)

    def judge_schedule(self, judge: Judge) -> list[ScheduledBlock]:
        return sorted((b for b in self.blocks if b.judge == judge), key=lambda b: b.start)

    def by_judge(self) -> dict[Judge, list[ScheduledBlock]]:
        out: dict[Judge, list[ScheduledBlock]] = defaultdict(list)
        for b in sorted(self.blocks, key=lambda b: b.start):
            out[b.judge].append(b)
        return dict(out)

    def scheduled_events(self) -> Iterable[tuple[JudgingEvent, ScheduledBlock, float]]:
        """Every event with its block and planned start time (minutes)."""
        for sb in self.blocks:
            for e, off in sb.block.offsets():
                yield e, sb, sb.start + off
