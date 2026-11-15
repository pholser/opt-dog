"""Read/write a show's dogs: the source of every breed's entry counts.

    entries.csv     dog, sex, status, exhibitor, judge, event: one row per dog in a breed event.
                    sex is D or B (blank if unknown; programs do not split non-regular classes
                    by sex); status is class, nonregular or champion; exhibitor is blank when no
                    one is on record, and such a dog never causes an exhibitor conflict
    exhibitors.csv  id, professional, registered, team: needed only when some entry names an
                    exhibitor. One row per person who takes dogs into the ring: each member of a
                    professional handler's team is an exhibitor of their own, opts in
                    (registered = 1) on their own, and ``team`` only labels them

An entry refers to its event by (judge, event name), unique within a show. A breed's entry total
and its ``dogs-bitches-(BOB dogs-BOB bitches)`` breakdown are counted from these rows, so a
program or problem directory has no counts of its own. Writing a show with no dogs on record
generates them from its events' counts, with no exhibitors.
"""

from __future__ import annotations

import csv
import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from optdog.domain import (
    Entry,
    EntryCounts,
    EntryStatus,
    Exhibitor,
    JudgingEvent,
    Sex,
    Show,
    anonymous_entries,
    entry_counts,
)

ENTRY_FIELDS = ("dog", "sex", "status", "exhibitor", "judge", "event")


def entries_of(show: Show) -> list[Entry]:
    """The show's dogs: those on record, and for any event without them, dogs generated from its counts."""
    have = {id(en.event) for en in show.entries}
    used = {en.dog for en in show.entries}
    ids = (d for d in (f"D{n:05d}" for n in itertools.count(1)) if d not in used)
    out = list(show.entries)
    for e in show.events:
        if id(e) not in have:
            out += anonymous_entries(e, ids)
    return out


def write_entries(entries: list[Entry], out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    exhibitors: dict[str, Exhibitor] = {}
    for en in entries:
        if en.exhibitor is not None:
            exhibitors.setdefault(en.exhibitor.id, en.exhibitor)
    with open(out / "entries.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(ENTRY_FIELDS)
        for en in entries:
            w.writerow([en.dog, en.sex.value if en.sex else "", en.status.value,
                        en.exhibitor.id if en.exhibitor else "", en.event.judge.name, en.event.name])
    path = out / "exhibitors.csv"
    if exhibitors:
        with open(path, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["id", "professional", "registered", "team"])
            for x in exhibitors.values():
                w.writerow([x.id, int(x.professional), int(x.registered), x.team or ""])
    elif path.exists():
        path.unlink()  # a stale file would name exhibitors no entry has


@dataclass(frozen=True)
class _Row:
    where: str
    dog: str
    sex: Sex | None
    status: EntryStatus
    exhibitor: Exhibitor | None


@dataclass
class EntryTable:
    """``entries.csv`` (and ``exhibitors.csv``) read and checked, before events exist."""

    rows: dict[tuple[str, str], list[_Row]] = field(default_factory=dict)  # (judge, event) -> dogs

    def counts(self, judge: str, event: str, where: str) -> tuple[int, EntryCounts | None]:
        """Entry total and breakdown for an event; ``where`` names the event's row for the error."""
        rows = self.rows.get((judge, event))
        if not rows:
            raise ValueError(f"{where}: {event!r} judged by {judge!r} has no dogs in entries.csv")
        return entry_counts((r.status, r.sex) for r in rows)

    def attach(self, events: list[JudgingEvent]) -> list[Entry]:
        """Entries on the given event objects; every row must name one of them."""
        by_key: dict[tuple[str, str], list[JudgingEvent]] = defaultdict(list)
        for e in events:
            by_key[e.judge.name, e.name].append(e)
        out = []
        for (judge, name), rows in self.rows.items():
            evs = by_key.get((judge, name))
            if not evs:
                raise ValueError(f"{rows[0].where}: no event {name!r} judged by {judge!r}")
            if len(evs) > 1:
                raise ValueError(f"{rows[0].where}: event {name!r} for {judge!r} is listed more than once")
            out += [Entry(r.dog, evs[0], r.exhibitor, r.status, r.sex) for r in rows]
        return out


def read_entry_table(in_dir: str | Path) -> EntryTable:
    """Raises ``ValueError`` naming the file and line for a bad reference or value."""
    d = Path(in_dir)

    def flag(value: str | None, where: str) -> bool:
        v = (value or "0").strip()
        if v not in ("0", "1"):
            raise ValueError(f"{where}: expected 0 or 1, got {value!r}")
        return v == "1"

    exhibitors: dict[str, Exhibitor] = {}
    if (d / "exhibitors.csv").exists():
        with open(d / "exhibitors.csv", newline="") as f:
            for i, r in enumerate(csv.DictReader(f), start=2):
                where = f"exhibitors.csv, line {i}"
                if not r.get("id"):
                    raise ValueError(f"{where}: no id")
                if r["id"] in exhibitors:
                    raise ValueError(f"{where}: exhibitor {r['id']!r} is listed twice")
                exhibitors[r["id"]] = Exhibitor(r["id"], flag(r.get("professional"), where),
                                                flag(r.get("registered"), where), r.get("team") or None)
    statuses = {s.value: s for s in EntryStatus}
    sexes = {s.value: s for s in Sex}
    table = EntryTable()
    with open(d / "entries.csv", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            where = f"entries.csv, line {i}"
            if not r.get("dog"):
                raise ValueError(f"{where}: no dog")
            if r.get("status") not in statuses:
                raise ValueError(f"{where}: status {r.get('status')!r} is not one of {sorted(statuses)}")
            sex = (r.get("sex") or "").strip()
            if sex and sex not in sexes:
                raise ValueError(f"{where}: sex {sex!r} is not D or B")
            x = (r.get("exhibitor") or "").strip()
            if x and x not in exhibitors:
                raise ValueError(f"{where}: exhibitor {x!r} is not in exhibitors.csv")
            table.rows.setdefault((r["judge"], r["event"]), []).append(
                _Row(where, r["dog"], sexes.get(sex), statuses[r["status"]], exhibitors.get(x)))
    return table


def read_entries(in_dir: str | Path, events: list[JudgingEvent]) -> list[Entry]:
    """Dogs from another directory (e.g. synthetic exhibitors) for events that already exist.
    Their counts must agree with the events', since they replace the dogs the counts came from."""
    table = read_entry_table(in_dir)
    entries = table.attach(events)
    for e in events:
        total, counts = table.counts(e.judge.name, e.name, "entries.csv") if (e.judge.name, e.name) in table.rows else (0, None)
        if (total, counts) != (e.entries, e.counts):
            raise ValueError(f"entries.csv: {e.name!r} judged by {e.judge.name!r} has {total} dogs "
                             f"{_fmt(counts)}; the show has {e.entries} {_fmt(e.counts)}")
    return entries


def _fmt(c: EntryCounts | None) -> str:
    return "" if c is None else f"({c.class_dogs}-{c.class_bitches}-({c.bob_dogs}-{c.bob_bitches}) +{c.nonregular})"
