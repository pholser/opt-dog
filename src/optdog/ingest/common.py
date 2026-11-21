"""What every superintendent's parser shares: the parse report, the raw listing records,
time and entry-count parsing, and the cross-check against a program's breed index.

A parser reads every event on a program into ``RawBlock``/``RawEvent`` records, so its counts can
be checked against the program's printed totals, and builds domain objects only for the regular
breed judging of the all-breed show (U 2026-11-13).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from optdog.domain import EntryCounts, Group, JudgingProgram, TimeOfDay

DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")

RE_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM|A\.M\.|P\.M\.|Noon|NOON)$", re.I)

GROUP_WORDS = {
    "sporting": Group.SPORTING,
    "hound": Group.HOUND,
    "hounds": Group.HOUND,
    "working": Group.WORKING,
    "terrier": Group.TERRIER,
    "terriers": Group.TERRIER,
    "toy": Group.TOY,
    "toys": Group.TOY,
    "nonsporting": Group.NON_SPORTING,
    "non-sporting": Group.NON_SPORTING,
    "herding": Group.HERDING,
}


def parse_time(s: str) -> TimeOfDay | None:
    m = RE_TIME.match(s.strip())
    if not m:
        return None
    h, mm, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).upper().replace(".", "")
    if ampm == "NOON":
        return TimeOfDay.at(12, mm)
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return TimeOfDay.at(h, mm)


def parse_counts(s: str) -> EntryCounts:
    """Parse ``dogs-bitches[-nonreg...]-(bobD-bobB)[-nonreg...]``.

    Non-regular tokens look like ``4``, ``4V``, ``1VD``, ``1 SD``, ``1BB``; any token
    starting with a number after the first two counts is a non-regular class.
    """
    bob_d = bob_b = 0
    m = re.search(r"\((\d+)-(\d+)\)", s)
    if m:
        bob_d, bob_b = int(m.group(1)), int(m.group(2))
        s = s[: m.start()] + "-" + s[m.end():]
    parts = [t for t in re.split(r"[-\s]+", s) if t]
    nums = [int(d.group()) for t in parts if (d := re.match(r"\d+", t))]
    nonreg = sum(nums[2:])
    return EntryCounts(nums[0], nums[1], bob_d, bob_b, nonreg)


@dataclass
class ParseReport:
    source: str
    day: str
    unparsed: list[str] = field(default_factory=list)
    unknown_breeds: list[str] = field(default_factory=list)
    checks: list[tuple[str, int, int]] = field(default_factory=list)  # (what, stated, parsed)
    notes: list[str] = field(default_factory=list)
    index_mismatches: list[str] = field(default_factory=list)
    not_kept: list[str] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[tuple[str, int, int]]:
        return [c for c in self.checks if c[1] != c[2]]


@dataclass(frozen=True)
class IndexRow:
    """One line of the program's breed index: 'Golden Retriever......37 9:50 AM 1'."""

    name: str
    entries: int
    time: TimeOfDay
    ring: int


@dataclass
class ParsedDay:
    program: JudgingProgram
    report: ParseReport


@dataclass
class RawBlock:
    ring: int
    judge: str
    start: TimeOfDay
    all_breed: bool  # in the all-breed show, not a special attraction or concurrent specialty
    events: list[RawEvent] = field(default_factory=list)


@dataclass
class RawEvent:
    name: str
    breed: bool  # regular breed judging, not sweepstakes, juniors, obedience, rally, ...
    entries: int
    counts: EntryCounts | None = None


def cross_check_index(report: ParseReport, blocks: list[RawBlock], index: list[IndexRow]) -> None:
    """Compare the back-of-program breed index with the parsed ring listings (every event)."""
    from optdog.breeds import name_key

    rows = defaultdict(list)
    for rb in blocks:
        for e in rb.events:
            rows[name_key(e.name)].append((e, rb))
    matched = 0
    skip = re.compile(
        r"(Junior|Intermediate|Senior|Master) Class|Novice|Open Class|Utility|Graduate|Preferred|Versatility|"
        r"Rally|Obedience|Sw(ee)?p|Swps|Sws\b|Vt |Head Type|Movement|Bred by",
        re.I,
    )
    index = [row for row in index if not skip.search(row.name)]
    for row in index:
        key = name_key(row.name)
        cands = rows.get(key) or [v for k, vs in rows.items() if k.startswith(key) or key.startswith(k) for v in vs]
        if not cands:
            report.index_mismatches.append(f"index lists {row.name!r} ({row.entries}, ring {row.ring}) but listings do not")
            continue
        ok = [(e, rb) for e, rb in cands if e.entries == row.entries and rb.start == row.time and rb.ring == row.ring]
        if ok:
            matched += 1
        else:
            e, rb = cands[0]
            report.index_mismatches.append(
                f"{row.name!r}: index {row.entries} @ {row.time} ring {row.ring}; "
                f"listing {e.entries} @ {rb.start} ring {rb.ring}"
            )
    report.checks.append(("breed index rows (breeds) matching listings", len(index), matched))
