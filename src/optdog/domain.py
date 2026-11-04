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


# --------------------------------------------------------------------------- exhibitors


class EntryStatus(Enum):
    """How a dog competes in its breed, which decides how long its exhibitor is busy (U)."""

    CLASS = "class"  # regular or non-regular class dog: busy for the whole breed event
    CHAMPION = "champion"  # Best of Breed competition only: busy only for the BOB portion


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
    """One dog entered in one breed event, shown by one exhibitor."""

    dog: str
    event: "JudgingEvent"
    exhibitor: Exhibitor
    status: EntryStatus

    @property
    def busy_offset(self) -> tuple[float, float]:
        """(from, to) minutes after the event starts during which the exhibitor is busy."""
        if self.status is EntryStatus.CHAMPION:
            return self.event.bob_offset, self.event.duration
        return 0.0, self.event.duration


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
    entries: list[Entry] = field(default_factory=list)  # individual dogs, when known (synthetic for now)
    pinned: list[ScheduledBlock] = field(default_factory=list)  # published commitments a program must keep (SPEC H11)
    # When judges' lunch breaks may start. Set ahead of time by the club, usually by when
    # the venue's concessions serve lunch; an input, not a scheduling decision.
    # None: use ShowParameters.lunch_window.
    lunch_window: tuple[Minutes, Minutes] | None = None

    def unpinned_events(self) -> list[JudgingEvent]:
        """Events the scheduler places: every event not inside a pinned block."""
        fixed = {id(e) for sb in self.pinned for e in sb.block.events}
        return [e for e in self.events if id(e) not in fixed]

    @property
    def exhibitors(self) -> list[Exhibitor]:
        seen: dict[str, Exhibitor] = {}
        for en in self.entries:
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

    def group_duration(self, group: Group) -> float:
        """Planned minutes to judge ``group``: entrants × the group judge's rate."""
        judge = self.group_judges.get(group)
        rate = judge.rate if judge else DEFAULT_MINUTES_PER_DOG
        return self.group_entrants(group) * rate

    def bis_entrants(self) -> int:
        """One group winner per variety group that has at least one breed entered."""
        return sum(1 for g in VARIETY_GROUPS if self.group_entrants(g) > 0)

    def bis_duration(self) -> float:
        rate = self.bis_judge.rate if self.bis_judge else DEFAULT_MINUTES_PER_DOG
        return self.bis_entrants() * rate

    def groups_and_bis_duration(self) -> float:
        """Total planned minutes of group + BIS judging if run back to back."""
        return sum(self.group_duration(g) for g in VARIETY_GROUPS) + self.bis_duration()


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

    def event_end(self, event: JudgingEvent) -> float | None:
        for e, _, start in self.scheduled_events():
            if e is event:
                return start + e.duration
        return None

    def group_timeline(self, judge_travel_minutes: float = 0.0) -> GroupTimeline | None:
        """Planned group and BIS times, run sequentially in the group ring(s).

        The first group is published to start at ``groups.start``; each later group
        follows the previous one. A group cannot start before its breeds are done and
        its judge has finished their breed blocks — plus ``judge_travel_minutes`` if
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
        judge_free: dict[Judge, float] = {
            j: sb.end + (0.0 if sb.ring in group_rings else judge_travel_minutes) for j, sb in last_block.items()
        }
        slots: list[GroupSlot] = []
        t = float(plan.start)
        for g in plan.order:
            breeds_done = max((ends[id(e)] for e in self.show.feeding(g) if id(e) in ends), default=0.0)
            judge = self.show.group_judges.get(g)
            ready = max(breeds_done, judge_free.get(judge, 0.0) if judge else 0.0)
            start = max(t, ready)
            end = start + self.show.group_duration(g)
            slots.append(GroupSlot(g, start, end, breeds_done, ready, available=t))
            t = end
        bis = self.show.bis_judge
        bis_start = max(t, judge_free.get(bis, 0.0) if bis else 0.0)
        return GroupTimeline(tuple(slots), bis_start, bis_start + self.show.bis_duration())


@dataclass(frozen=True)
class GroupSlot:
    group: Group
    start: float
    end: float
    breeds_done: float  # when the group's last breed finishes
    ready: float  # max(breeds_done, group judge free)
    available: float  # when the group ring could have taken this group (previous end / published start)

    @property
    def wait(self) -> float:
        """Minutes the group ring sat idle waiting for this group's breeds or judge."""
        return self.start - self.available


@dataclass(frozen=True)
class GroupTimeline:
    groups: tuple[GroupSlot, ...]
    bis_start: float
    bis_end: float

    @property
    def finish(self) -> float:
        return self.bis_end
