"""Where shows, entries and solved schedules come from, independent of format or storage.

The rest of the package names a show by a ``ShowKey`` and asks a source for domain objects; it
never sees a path or a file format. Each kind of data has a read protocol and a write protocol,
so a read-only source (a parser of some other format, a remote service) need not fake writes,
and one backend may implement any of them:

    ProgramSource / ProgramSink   published (or generated) judging programs
    EntrySource / EntrySink       a show's dogs and exhibitors, per synthetic seed
    ResultSource / ResultSink     solver results by step, and the relaxation's lower bound

Backends: ``io.csv_dataset.CsvDataset`` (the CSV and JSON directories under data/) and
``MemoryDataset`` here. A source for another format (JSON, Excel, a database) implements the
same protocols.

The functions at the bottom combine sources into what the analysis needs: a day's published
program with its entries, the instance built from it, and its saved results matched back to
that instance's own objects.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Protocol

from optdog.domain import (
    Block,
    Break,
    Entry,
    GroupsPlan,
    JudgingEvent,
    JudgingProgram,
    ScheduledBlock,
    Show,
)
from optdog.instance import instance_from_program
from optdog.solve.cpsat import PlacementResult
from optdog.solve.relaxation import RelaxedBound

STEPS = ("placement", "formation", "ordering", "packing")


@dataclass(frozen=True, order=True)
class ShowKey:
    """One show day: who published it (``source``, e.g. "bradshaw") and its name within that."""

    source: str
    slug: str  # e.g. "2025-11-14-tucson-kennel-club"

    def __str__(self) -> str:
        return f"{self.source}/{self.slug}"


# ---------------------------------------------------------------------------- protocols


class ProgramSource(Protocol):
    def program_keys(self) -> list[ShowKey]:
        """Every show this source holds a program for, sorted."""
        ...

    def program(self, key: ShowKey) -> JudgingProgram: ...


class ProgramSink(Protocol):
    def put_program(self, key: ShowKey, program: JudgingProgram, origin: str = "") -> None:
        """Store a program; ``origin`` says where it came from (a source file, a solver)."""
        ...


class EntrySource(Protocol):
    def entries(self, key: ShowKey, events: list[JudgingEvent], seed: int = 0) -> list[Entry] | None:
        """The show's dogs for ``seed``, attached to ``events`` (matched by judge and event name),
        or None when there are none on record."""
        ...


class EntrySink(Protocol):
    def put_entries(self, key: ShowKey, entries: list[Entry], seed: int = 0) -> None: ...


class ResultSource(Protocol):
    def bound(self, key: ShowKey) -> RelaxedBound | None: ...

    def result(self, key: ShowKey, step: str) -> PlacementResult | None:
        """A saved result as stored, on objects of its own: see ``result_on``."""
        ...


class ResultSink(Protocol):
    def put_bound(self, key: ShowKey, bound: RelaxedBound) -> None: ...

    def put_result(self, key: ShowKey, step: str, result: PlacementResult) -> None: ...


# ---------------------------------------------------------------------------- in memory


@dataclass
class MemoryDataset:
    """Every protocol, held in dictionaries: for tests, and for data built in code."""

    programs: dict[ShowKey, JudgingProgram] = field(default_factory=dict)
    dogs: dict[tuple[ShowKey, int], list[Entry]] = field(default_factory=dict)
    bounds: dict[ShowKey, RelaxedBound] = field(default_factory=dict)
    results: dict[tuple[ShowKey, str], PlacementResult] = field(default_factory=dict)

    def program_keys(self) -> list[ShowKey]:
        return sorted(self.programs)

    def program(self, key: ShowKey) -> JudgingProgram:
        return self.programs[key]

    def put_program(self, key: ShowKey, program: JudgingProgram, origin: str = "") -> None:
        self.programs[key] = program

    def entries(self, key: ShowKey, events: list[JudgingEvent], seed: int = 0) -> list[Entry] | None:
        dogs = self.dogs.get((key, seed))
        if dogs is None:
            return None
        by_key = {(e.judge.name, e.name): e for e in events}
        return [replace(en, event=by_key[en.event.judge.name, en.event.name]) for en in dogs]

    def put_entries(self, key: ShowKey, entries: list[Entry], seed: int = 0) -> None:
        self.dogs[key, seed] = list(entries)

    def bound(self, key: ShowKey) -> RelaxedBound | None:
        return self.bounds.get(key)

    def result(self, key: ShowKey, step: str) -> PlacementResult | None:
        return self.results.get((key, step))

    def put_bound(self, key: ShowKey, bound: RelaxedBound) -> None:
        self.bounds[key] = bound

    def put_result(self, key: ShowKey, step: str, result: PlacementResult) -> None:
        self.results[key, step] = result


# ---------------------------------------------------------------------------- combining sources


def published_day(programs: ProgramSource, entries: EntrySource, key: ShowKey,
                  seed: int = 0) -> tuple[JudgingProgram, Show]:
    """The published program, with the dogs for ``seed`` if any are on record, and its instance."""
    human = programs.program(key)
    dogs = entries.entries(key, human.show.events, seed)
    if dogs is not None:
        human.show.entries = dogs
    return human, instance_from_program(human)


def _event_key(e: JudgingEvent) -> tuple[str, str]:
    return e.judge.name, e.name


def _pin_key(sb: ScheduledBlock) -> tuple:
    return sb.ring.number, sb.start, sb.judge.name, tuple(e.name for e in sb.block.events)


def program_on(saved: JudgingProgram, show: Show) -> JudgingProgram:
    """A saved schedule rebuilt from ``show``'s own events, judges, rings and pinned blocks, so
    the validator sees the same instance the solver did."""
    events: dict[tuple, list[JudgingEvent]] = defaultdict(list)
    for e in show.events:
        events[_event_key(e)].append(e)
    pinned = {_pin_key(sb): sb for sb in show.pinned}
    judges = {j.name: j for j in show.judges}
    rings = {r.number: r for r in show.rings}
    blocks = []
    for sb in saved.blocks:
        pin = pinned.get(_pin_key(sb))
        if pin is not None:
            blocks.append(pin)
            continue
        evs = tuple(events[_event_key(e)].pop(0) for e in sb.block.events)
        blocks.append(ScheduledBlock(Block(evs[0].judge, evs), rings.get(sb.ring.number, sb.ring), sb.start))
    breaks = [Break(judges[br.judge.name], br.ring, br.start, br.length) for br in saved.breaks]
    plan = saved.groups
    if plan is None:
        raise ValueError(f"{show.name}: the saved schedule has no groups")
    groups = GroupsPlan(show.group_rings, plan.start, plan.order, dict(show.group_judges), show.bis_judge)
    return JudgingProgram(show, blocks, breaks, groups)


def result_on(saved: PlacementResult, show: Show) -> PlacementResult:
    return replace(saved, program=program_on(saved.program, show))


@dataclass
class SolvedDay:
    key: ShowKey
    human: JudgingProgram
    show: Show
    bound: RelaxedBound
    results: dict[str, PlacementResult]

    @property
    def name(self) -> str:
        return self.key.slug


def solved_day(programs: ProgramSource, entries: EntrySource, results: ResultSource, key: ShowKey) -> SolvedDay:
    """A day's published program, its instance, and every saved step on that instance."""
    bound = results.bound(key)
    if bound is None:
        raise LookupError(f"{key} has no saved results: run python -m optdog.analysis.solve_all")
    human, show = published_day(programs, entries, key)
    saved = {s: results.result(key, s) for s in STEPS}
    missing = [s for s, r in saved.items() if r is None]
    if missing:
        raise LookupError(f"{key} has no saved {', '.join(missing)}")
    return SolvedDay(key, human, show, bound, {s: result_on(r, show) for s, r in saved.items() if r is not None})
