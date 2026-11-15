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

Times of day are ``TimeOfDay`` and lengths of time ``Duration``: neither mixes with plain
numbers, so a length cannot be used as a time or the reverse. This module has no I/O or solver
dependencies.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import ClassVar, Iterable, Iterator

# --------------------------------------------------------------------------- time


@dataclass(frozen=True, order=True, slots=True)
class Duration:
    """A length of time in minutes, fractional where judging rates make it so (2.4 min a dog)."""

    minutes: float

    ZERO: ClassVar[Duration]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Duration):
            return self.minutes == other.minutes
        if isinstance(other, (int, float)):
            raise TypeError(f"compare a Duration with a Duration, not {other!r}")
        return NotImplemented

    def __add__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.minutes + other.minutes)

    def __sub__(self, other: Duration) -> Duration:
        if not isinstance(other, Duration):
            return NotImplemented
        return Duration(self.minutes - other.minutes)

    def __mul__(self, k: float) -> Duration:
        if not isinstance(k, (int, float)):
            return NotImplemented
        return Duration(self.minutes * k)

    __rmul__ = __mul__

    def __truediv__(self, other):
        """By a number, a Duration; by a Duration, their ratio."""
        if isinstance(other, Duration):
            return self.minutes / other.minutes
        if isinstance(other, (int, float)):
            return Duration(self.minutes / other)
        return NotImplemented

    def __neg__(self) -> Duration:
        return Duration(-self.minutes)

    def __abs__(self) -> Duration:
        return Duration(abs(self.minutes))

    def __bool__(self) -> bool:
        return self.minutes != 0

    def __str__(self) -> str:
        return f"{self.minutes:g} min"

    @classmethod
    def total(cls, durations: Iterable[Duration]) -> Duration:
        """Their sum, correctly rounded (as ``math.fsum``), so 25 dogs at 2.4 min are 60 min exactly."""
        return cls(math.fsum(d.minutes for d in durations))

    @classmethod
    def from_units(cls, units: float, per_minute: int) -> Duration:
        """From a solver's integer time units, ``per_minute`` to a minute."""
        return cls(units / per_minute)

    def units(self, per_minute: int) -> int:
        """In a solver's integer time units, ``per_minute`` to a minute, to the nearest unit."""
        return round(self.minutes * per_minute)


Duration.ZERO = Duration(0.0)


@dataclass(frozen=True, order=True, slots=True)
class TimeOfDay:
    """A time on the show day, in minutes after midnight (fractional for planned event ends).

    A time plus or minus a ``Duration`` is a time; one time minus another is a ``Duration``.
    """

    minutes: float

    MIDNIGHT: ClassVar[TimeOfDay]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TimeOfDay):
            return self.minutes == other.minutes
        if isinstance(other, (int, float)):
            raise TypeError(f"compare a TimeOfDay with a TimeOfDay, not {other!r}")
        return NotImplemented

    @classmethod
    def at(cls, hour: int, minute: int = 0) -> TimeOfDay:
        return cls(hour * 60 + minute)

    @classmethod
    def parse(cls, hhmm: str) -> TimeOfDay:
        """From 24-hour ``HH:MM``."""
        h, m = hhmm.strip().split(":")
        return cls(int(h) * 60 + int(m))

    @classmethod
    def from_units(cls, units: float, per_minute: int) -> TimeOfDay:
        """From a solver's integer time units, ``per_minute`` to a minute."""
        return cls(units / per_minute)

    def units(self, per_minute: int) -> int:
        """In a solver's integer time units, ``per_minute`` to a minute, to the nearest unit."""
        return round(self.minutes * per_minute)

    def __add__(self, d: Duration) -> TimeOfDay:
        if not isinstance(d, Duration):
            return NotImplemented
        return TimeOfDay(self.minutes + d.minutes)

    __radd__ = __add__

    def __sub__(self, other):
        """Minus a Duration, a time; minus a time, the Duration between them."""
        if isinstance(other, TimeOfDay):
            return Duration(self.minutes - other.minutes)
        if isinstance(other, Duration):
            return TimeOfDay(self.minutes - other.minutes)
        return NotImplemented

    def on_grid(self, step: Duration) -> bool:
        return self.minutes % step.minutes == 0

    def ceil(self, step: Duration) -> TimeOfDay:
        """The first time on the ``step`` grid at or after this one."""
        return TimeOfDay(math.ceil(self.minutes / step.minutes - 1e-9) * step.minutes)

    def hhmm(self) -> str:
        """24-hour ``HH:MM``, to the nearest minute."""
        h, m = divmod(round(self.minutes), 60)
        return f"{h:02d}:{m:02d}"

    def __str__(self) -> str:
        """``8:30 AM``, to the nearest minute."""
        h, m = divmod(round(self.minutes), 60)
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


TimeOfDay.MIDNIGHT = TimeOfDay(0.0)


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


class RingSize(IntEnum):
    """Breed ring size classes, from the Show Manual's suggested ring sizes (§3, "Breed Rings",
    width × unobstructed length in feet). A larger class can host anything a smaller one can."""

    S = 1  # 24 × 32: all Toy breeds
    M = 2  # 32 × 40: Terrier, Hound and Non-Sporting breeds
    L = 3  # 40 × 48: Sporting, Working and Herding breeds; "larger breeds" of the others (32 × 50)
    XL = 4  # 48 × 64: specialty shows; here, group rings ("enlarged or combined", §3 "Group Rings")


def default_min_ring(group: "Group | None", examination: Examination) -> RingSize:
    """The smallest ring a breed needs, by rule: Toy breeds S; otherwise breeds examined on a table
    or ramp M and breeds examined on the ground L (U, 2026-11-08). The Show Manual sizes rings by
    group and gives Terrier, Hound and Non-Sporting "larger breeds" more length without saying which
    breeds; AKC's table/ramp list stands in for size."""
    if group is Group.TOY:
        return RingSize.S
    if examination in (Examination.TABLE, Examination.RAMP, Examination.TABLE_OR_RAMP):
        return RingSize.M
    return RingSize.L


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
    ring_override: RingSize | None = None  # the catalog's min_ring column, where it departs from the rule

    @property
    def min_ring(self) -> RingSize:
        return self.ring_override or default_min_ring(self.group, self.examination)

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


DEFAULT_PER_DOG = Duration(60 / 25)  # Rules Ch.7 §12: about 25 dogs per hour
PERMIT_PER_DOG = Duration(60 / 20)  # Show Manual §6: permit/sweeps judges about 20/hr


@dataclass(frozen=True)
class Judge:
    name: str
    permit: bool = False
    per_dog: Duration | None = None  # per-judge override of the time a dog takes

    @property
    def rate(self) -> Duration:
        """Time per dog."""
        if self.per_dog is not None:
            return self.per_dog
        return PERMIT_PER_DOG if self.permit else DEFAULT_PER_DOG

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
    per_entry: Duration | None = None  # overrides the judge's rate (e.g. juniors)

    @property
    def rate(self) -> Duration:
        if self.per_entry is not None:
            return self.per_entry
        if self.kind is EventKind.JUNIOR_SHOWMANSHIP:
            return PERMIT_PER_DOG  # Show Manual §6: juniors at 20 per hour
        return self.judge.rate

    @property
    def min_ring(self) -> RingSize:
        """The smallest ring this event can be judged in (H16). Events without a breed (juniors,
        puppy groups, obedience) need M."""
        return self.breed.min_ring if self.breed is not None else RingSize.M

    @property
    def duration(self) -> Duration:
        """Planned time to judge this event."""
        return self.rate * self.entries

    @property
    def bob_offset(self) -> Duration:
        """Time from the event's start until Best of Breed (champions) begins."""
        if self.counts is None:
            return Duration.ZERO
        return self.rate * self.counts.before_bob

    @property
    def group(self) -> Group | None:
        """The breed's AKC group (including MISCELLANEOUS) for breed events; else None."""
        return self.breed.group if self.breed and self.kind is EventKind.BREED else None

    @property
    def equipment(self) -> str | None:
        """'table', 'ramp' or 'ground' for a breed event (S3); None for anything else."""
        if self.kind is not EventKind.BREED or self.breed is None:
            return None
        ex = self.breed.examination
        return "table" if ex.needs_table else "ramp" if ex.needs_ramp else "ground"

    @property
    def feeds_group(self) -> Group | None:
        """The variety group this event must finish before, or None (Rules: breeds before their group)."""
        g = self.group
        return g if g is not None and g.is_variety_group else None

    def __str__(self) -> str:
        return f"{self.entries} {self.name}"


# --------------------------------------------------------------------------- exhibitors


class EntryStatus(Enum):
    """How a dog competes in its breed, which decides when it is judged and how long its exhibitor
    is busy (U). Judging order: regular class dogs, then bitches, then non-regular classes, then
    Best of Breed."""

    CLASS = "class"  # regular class dog: busy for the whole breed event
    NONREGULAR = "nonregular"  # non-regular class (veterans, etc.): busy for the whole breed event, too
    CHAMPION = "champion"  # Best of Breed competition only: busy only for the BOB portion


class Sex(Enum):
    DOG = "D"
    BITCH = "B"


@dataclass(frozen=True)
class Exhibitor:
    """Whoever takes a dog into the ring (the exhibitor marked on the entry).

    An owner-handler, or a member of a professional handler's team: the handler or one
    of their assistants. When a professional needs help, the assistant who takes the dog
    in is the exhibitor on that dog (user decision), and assistants may register for
    non-conflict accommodation like anyone else.
    """

    id: str
    professional: bool = False
    registered: bool = False  # registered for non-conflict accommodation (only these count in S1)
    team: str | None = None  # professional handler team this person belongs to

    def __str__(self) -> str:
        return self.id


@dataclass(frozen=True)
class Entry:
    """One dog entered in one breed event.

    ``exhibitor`` is None when no one is on record (e.g. entries generated from a published
    program's counts): such a dog never causes an exhibitor conflict. ``sex`` is None when it is
    not known; published programs do not split non-regular classes by sex.
    """

    dog: str
    event: "JudgingEvent"
    exhibitor: Exhibitor | None
    status: EntryStatus
    sex: Sex | None = None

    @property
    def busy_offset(self) -> tuple[Duration, Duration]:
        """(from, to) after the event starts: when the exhibitor is busy."""
        if self.status is EntryStatus.CHAMPION:
            return self.event.bob_offset, self.event.duration
        return Duration.ZERO, self.event.duration


def entry_counts(statuses: Iterable[tuple[EntryStatus, Sex | None]]) -> tuple[int, EntryCounts | None]:
    """An event's entry total and breakdown from its dogs' (status, sex). The breakdown is None
    when a class dog or champion has no sex, since the program's ``dogs-bitches-(BOB)`` form needs it."""
    rows = list(statuses)
    n: dict[tuple[EntryStatus, Sex | None], int] = defaultdict(int)
    for st, sex in rows:
        n[st, None if st is EntryStatus.NONREGULAR else sex] += 1
    if any(n[st, None] for st in (EntryStatus.CLASS, EntryStatus.CHAMPION)):
        return len(rows), None
    c, ch = EntryStatus.CLASS, EntryStatus.CHAMPION
    return len(rows), EntryCounts(n[c, Sex.DOG], n[c, Sex.BITCH], n[ch, Sex.DOG], n[ch, Sex.BITCH],
                                  n[EntryStatus.NONREGULAR, None])


def anonymous_entries(event: JudgingEvent, dog_ids: Iterator[str]) -> list[Entry]:
    """Dogs for ``event`` as its counts describe them, with no exhibitor: class dogs, class
    bitches, non-regular entries, then champion dogs and bitches. Without a breakdown, every dog
    is a class dog of unknown sex."""
    c = event.counts
    spec: list[tuple[EntryStatus, Sex | None, int]]
    if c is None:
        spec = [(EntryStatus.CLASS, None, event.entries)]
    else:
        spec = [(EntryStatus.CLASS, Sex.DOG, c.class_dogs), (EntryStatus.CLASS, Sex.BITCH, c.class_bitches),
                (EntryStatus.NONREGULAR, None, c.nonregular),
                (EntryStatus.CHAMPION, Sex.DOG, c.bob_dogs), (EntryStatus.CHAMPION, Sex.BITCH, c.bob_bitches)]
    return [Entry(next(dog_ids), event, None, st, sex) for st, sex, k in spec for _ in range(k)]


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
    def duration(self) -> Duration:
        return Duration.total(e.duration for e in self.events)

    @property
    def min_ring(self) -> RingSize:
        return max(e.min_ring for e in self.events)

    def offsets(self) -> Iterator[tuple[JudgingEvent, Duration]]:
        """Each event with its planned start offset from the block start."""
        t = Duration.ZERO
        for e in self.events:
            yield e, t
            t += e.duration


@dataclass(frozen=True)
class Ring:
    number: int
    size: RingSize = field(default=RingSize.XL, compare=False)  # a show input; unknown means adequate for anything

    def __str__(self) -> str:
        return f"Ring {self.number}"


@dataclass(frozen=True)
class ScheduledBlock:
    block: Block
    ring: Ring
    start: TimeOfDay  # published "not before" time

    @property
    def judge(self) -> Judge:
        return self.block.judge

    @property
    def end(self) -> TimeOfDay:
        return self.start + self.block.duration


@dataclass(frozen=True)
class Break:
    """A judge's meal/rest break (Rules Ch.7 §12: 45–60 min if assignment > 5 hours)."""

    judge: Judge
    ring: Ring | None
    start: TimeOfDay
    length: Duration | None = None  # None when the program does not state the length

    @property
    def end(self) -> TimeOfDay | None:
        return None if self.length is None else self.start + self.length


# --------------------------------------------------------------------------- groups


@dataclass(frozen=True)
class GroupsPlan:
    """Group judging and Best in Show, run sequentially in the group ring(s).

    ``order`` contains the seven variety groups only (``VARIETY_GROUPS``); the
    Miscellaneous Class has no group judging.
    """

    rings: tuple[Ring, ...]
    start: TimeOfDay | None
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
    start: TimeOfDay = field(kw_only=True)  # when breed judging may begin: the first published block
    entries: list[Entry] = field(default_factory=list)  # individual dogs, when known
    pinned: list[ScheduledBlock] = field(default_factory=list)  # published commitments a program must keep (SPEC H11)
    # When judges' lunch breaks may start. Set ahead of time by the club, usually by when
    # the venue's concessions serve lunch; an input, not a scheduling decision.
    # None: use ShowParameters.lunch_window.
    lunch_window: tuple[TimeOfDay, TimeOfDay] | None = None

    def unpinned_events(self) -> list[JudgingEvent]:
        """Events the scheduler places: every event not inside a pinned block."""
        fixed = {id(e) for sb in self.pinned for e in sb.block.events}
        return [e for e in self.events if id(e) not in fixed]

    @property
    def exhibitors(self) -> list[Exhibitor]:
        seen: dict[str, Exhibitor] = {}
        for en in self.entries:
            if en.exhibitor is not None:
                seen.setdefault(en.exhibitor.id, en.exhibitor)
        return list(seen.values())

    def events_for(self, judge: Judge) -> list[JudgingEvent]:
        return [e for e in self.events if e.judge == judge]

    def judge_load(self, judge: Judge) -> int:
        """Entries counting toward the AKC 175/day limit (Rules Ch.7 §13)."""
        return sum(e.entries for e in self.events_for(judge) if e.competition in _COUNTS_TOWARD_LIMIT)

    def breeds_in(self, group: Group) -> list[JudgingEvent]:
        """Breed events of an AKC group (MISCELLANEOUS included)."""
        return [e for e in self.events if e.group is group]

    def feeding(self, group: Group) -> list[JudgingEvent]:
        """All-breed show events that must finish before ``group`` is judged.

        Empty for MISCELLANEOUS. Concurrent specialty shows' winners do not go to
        the all-breed group, so only ALL_BREED events feed a group.
        """
        return [e for e in self.events if e.feeds_group is group and e.competition is Competition.ALL_BREED]

    # ------------------------------------------------------------ group / BIS sizing
    #
    # Model assumption (user decision): at most one winner per breed or variety
    # (BOB/BOV) competes in its group, so a group's size is the number of its breeds
    # and varieties entered in the all-breed show. BIS has one competitor per variety
    # group with at least one breed entered. Each is judged at its judge's (breed)
    # rate. In reality not every breed winner competes in group, so these are
    # conservative upper bounds; absolute group/BIS end times are not a calibration
    # target (on real shows they run hours past actual show close).

    def group_entrants(self, group: Group) -> int:
        """Breeds/varieties of ``group`` entered in the all-breed show (one BOB/BOV each)."""
        return len(
            {
                e.breed
                for e in self.feeding(group)
                if e.competition is Competition.ALL_BREED and e.entries > 0
            }
        )

    def group_duration(self, group: Group) -> Duration:
        """Planned time to judge ``group``: entrants × the group judge's rate."""
        judge = self.group_judges.get(group)
        rate = judge.rate if judge else DEFAULT_PER_DOG
        return rate * self.group_entrants(group)

    def bis_entrants(self) -> int:
        """One group winner per variety group that has at least one breed entered."""
        return sum(1 for g in VARIETY_GROUPS if self.group_entrants(g) > 0)

    def bis_duration(self) -> Duration:
        rate = self.bis_judge.rate if self.bis_judge else DEFAULT_PER_DOG
        return rate * self.bis_entrants()

    def groups_and_bis_duration(self) -> Duration:
        """Total planned time of group + BIS judging if run back to back."""
        return Duration.total(self.group_duration(g) for g in VARIETY_GROUPS) + self.bis_duration()


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

    def scheduled_events(self) -> Iterable[tuple[JudgingEvent, ScheduledBlock, TimeOfDay]]:
        """Every event with its block and planned start time."""
        for sb in self.blocks:
            for e, off in sb.block.offsets():
                yield e, sb, sb.start + off

    def event_end(self, event: JudgingEvent) -> TimeOfDay | None:
        for e, _, start in self.scheduled_events():
            if e is event:
                return start + e.duration
        return None

    def group_timeline(self, judge_travel: Duration = Duration.ZERO) -> GroupTimeline | None:
        """Planned group and BIS times, run sequentially in the group ring(s).

        The first group is published to start at ``groups.start``; each later group
        follows the previous one. A group cannot start before its breeds are done and
        its judge has finished their breed blocks — plus ``judge_travel`` if
        the judge's last block was outside the group ring(s). BIS follows the last
        group and cannot start before the BIS judge is free on the same terms.
        """
        plan = self.groups
        if plan is None or plan.start is None:
            return None
        ends = {id(e): start + e.duration for e, _, start in self.scheduled_events()}
        last_block: dict[Judge, ScheduledBlock] = {}
        for sb in self.blocks:
            if sb.judge not in last_block or sb.end > last_block[sb.judge].end:
                last_block[sb.judge] = sb
        group_rings = set(plan.rings)
        judge_free: dict[Judge, TimeOfDay] = {
            j: sb.end + (Duration.ZERO if sb.ring in group_rings else judge_travel) for j, sb in last_block.items()
        }
        slots: list[GroupSlot] = []
        t = plan.start
        never = TimeOfDay.MIDNIGHT
        for g in plan.order:
            breeds_done = max((ends[id(e)] for e in self.show.feeding(g) if id(e) in ends), default=never)
            judge = self.show.group_judges.get(g)
            ready = max(breeds_done, judge_free.get(judge, never) if judge else never)
            start = max(t, ready)
            end = start + self.show.group_duration(g)
            slots.append(GroupSlot(g, start, end, breeds_done, ready, available=t))
            t = end
        bis = self.show.bis_judge
        bis_start = max(t, judge_free.get(bis, never) if bis else never)
        return GroupTimeline(tuple(slots), bis_start, bis_start + self.show.bis_duration())


@dataclass(frozen=True)
class GroupSlot:
    group: Group
    start: TimeOfDay
    end: TimeOfDay
    breeds_done: TimeOfDay  # when the group's last breed finishes
    ready: TimeOfDay  # max(breeds_done, group judge free)
    available: TimeOfDay  # when the group ring could have taken this group (previous end / published start)

    @property
    def wait(self) -> Duration:
        """How long the group ring sat idle waiting for this group's breeds or judge."""
        return self.start - self.available


@dataclass(frozen=True)
class GroupTimeline:
    groups: tuple[GroupSlot, ...]
    bis_start: TimeOfDay
    bis_end: TimeOfDay

    @property
    def finish(self) -> TimeOfDay:
        return self.bis_end
