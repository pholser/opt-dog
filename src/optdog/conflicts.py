"""Exhibitor conflicts (SPEC S1): minutes a registered exhibitor is needed in two rings at once.

Busy windows (SPEC §2.7, user decision):
- showing a CLASS dog, the exhibitor is busy for the whole breed event;
- showing a CHAMPION, only for the Best of Breed portion at the end.

Conflicts are counted **by breed** (user decision): an exhibitor's dogs in one breed
event mean one presence in one ring, so they merge into a single busy window (any
class dog → the whole event; champions only → just the Best of Breed portion).
Several dogs in the same breed event are never a conflict. For each registered
exhibitor, every pair of their *breed* windows costs the minutes the two windows
overlap. When the events are in different
rings, one window is widened by the exhibitor travel margin on both sides, so
back-to-back events in different rings with less than the margin between them
still conflict. Two events in the same ring are judged one after the other, so
their (un-widened) windows do not overlap.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from optdog.domain import Entry, Exhibitor, JudgingProgram, Ring


@dataclass(frozen=True)
class BusyWindow:
    """One exhibitor's presence in one breed event (all their dogs in it, merged)."""

    exhibitor: Exhibitor
    entries: tuple[Entry, ...]
    start: float
    end: float
    ring: Ring

    @property
    def event(self):
        return self.entries[0].event


@dataclass(frozen=True)
class Conflict:
    exhibitor: Exhibitor
    a: BusyWindow
    b: BusyWindow
    minutes: float


@dataclass
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)
    unscheduled: list[Entry] = field(default_factory=list)  # entries whose event is not in the program

    @property
    def minutes(self) -> float:
        return sum(c.minutes for c in self.conflicts)

    def by_exhibitor(self) -> dict[Exhibitor, float]:
        out: dict[Exhibitor, float] = defaultdict(float)
        for c in self.conflicts:
            out[c.exhibitor] += c.minutes
        return dict(out)


def busy_windows(program: JudgingProgram) -> tuple[list[BusyWindow], list[Entry]]:
    """One window per (exhibitor, breed event): the span covering all their dogs in it."""
    timing = {id(e): (start, sb.ring) for e, sb, start in program.scheduled_events()}
    grouped: dict[tuple[str, int], list[Entry]] = defaultdict(list)
    missing = []
    for en in program.show.entries:
        if id(en.event) not in timing:
            missing.append(en)
        else:
            grouped[(en.exhibitor.id, id(en.event))].append(en)
    windows = []
    for ens in grouped.values():
        start, ring = timing[id(ens[0].event)]
        lo = min(en.busy_offset[0] for en in ens)
        hi = max(en.busy_offset[1] for en in ens)
        windows.append(BusyWindow(ens[0].exhibitor, tuple(ens), start + lo, start + hi, ring))
    return windows, missing


def overlap_minutes(a: BusyWindow, b: BusyWindow, travel_minutes: float) -> float:
    margin = travel_minutes if a.ring != b.ring else 0.0
    return max(0.0, min(a.end + margin, b.end) - max(a.start - margin, b.start))


def exhibitor_conflicts(program: JudgingProgram, travel_minutes: float, registered_only: bool = True) -> ConflictReport:
    windows, missing = busy_windows(program)
    report = ConflictReport(unscheduled=missing)
    by_exhibitor: dict[str, list[BusyWindow]] = defaultdict(list)
    for w in windows:
        if w.exhibitor.registered or not registered_only:
            by_exhibitor[w.exhibitor.id].append(w)
    for ws in by_exhibitor.values():
        for a, b in combinations(ws, 2):  # windows are per breed event, so a and b are different breeds
            m = overlap_minutes(a, b, travel_minutes)
            if m > 0:
                report.conflicts.append(Conflict(a.exhibitor, a, b, m))
    return report
