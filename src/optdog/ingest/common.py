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

from dataclasses import replace
from pathlib import Path

from optdog.breeds import BreedCatalog
from optdog.domain import (
    Block,
    Break,
    EntryCounts,
    Group,
    GroupsPlan,
    JudgingEvent,
    JudgingProgram,
    Judge,
    Ring,
    ScheduledBlock,
    Show,
    TimeOfDay,
)

DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")

RE_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM|A\.M\.|P\.M\.|Noon|NOON)$", re.I)

NUMBER_WORDS = {w: i for i, w in enumerate(
    "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN "
    "SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY".split(), 1)}

TITLES = re.compile(r"^(Mr|Mrs|Ms|Miss|Dr)\.?\s+", re.I)

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


def clock(s: str) -> TimeOfDay | None:
    """'6:30 PM', '1:00 P.M.', '12:00 NOON' -> a time of day."""
    s = re.sub(r"\s*([AP])\.?M\.?$", r" \1M", s.strip(), flags=re.I)
    return parse_time(s)


def norm_name(name: str) -> str:
    """A judge's name without title, periods or case, for matching across the program."""
    return " ".join(TITLES.sub("", name.strip().lstrip("*").strip()).replace(".", "").lower().split())


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


@dataclass
class GroupsTable:
    """The Variety Groups table as printed: rings, start, order with judges' names, and BIS."""

    rings: tuple[int, ...]
    start: TimeOfDay | None
    order: list[tuple[Group, str]] = field(default_factory=list)
    bis: str | None = None


def assemble(
    pdf_path: str | Path,
    show_name: str,
    day_name: str,
    blocks: list[RawBlock],
    breaks_raw: list[tuple[int, str, TimeOfDay]],
    groups: GroupsTable | None,
    closing: TimeOfDay | None,
    catalog: BreedCatalog,
    report: ParseReport,
    breakdown_check: str = "breakdown",
    display_name=lambda raw: raw.strip().lstrip("*").strip(),
) -> ParsedDay:
    """Build the domain program from the raw listings: only the regular breed judging of the
    all-breed show (U 2026-11-13), with its rings, judges, lunches and groups.

    A breed's printed entry count stands: it agrees with the ring totals and the index. Its
    breakdown (regular classes and BOB) is checked against it, and the rest of the entries are
    non-regular classes; a breakdown larger than the entries is dropped. Judges are matched
    across the program by ``norm_name``; ``display_name`` cleans the name printed in a listing.
    """
    judges: dict[str, Judge] = {}

    def judge_of(raw: str) -> Judge:
        key = norm_name(raw)
        if key not in judges:
            judges[key] = Judge(name=display_name(raw))
        return judges[key]

    rings: dict[int, Ring] = {}

    def ring_of(n: int) -> Ring:
        return rings.setdefault(n, Ring(n))

    events: list[JudgingEvent] = []
    scheduled: list[ScheduledBlock] = []
    for rb in blocks:
        j = judge_of(rb.judge)
        evs = []
        for raw in rb.events:
            counts = raw.counts
            if counts is not None:
                if counts.total != raw.entries:
                    report.checks.append((f"{breakdown_check} {raw.name}", raw.entries, counts.total))
                regular = counts.total - counts.nonregular
                counts = replace(counts, nonregular=raw.entries - regular) if regular <= raw.entries else None
            if not (rb.all_breed and raw.breed):
                continue
            breed = catalog.get(raw.name)
            if breed is None:
                report.unknown_breeds.append(raw.name)
            evs.append(JudgingEvent(raw.name, j, raw.entries, breed, counts))
        events += evs
        if evs:
            scheduled.append(ScheduledBlock(Block(j, tuple(evs)), ring_of(rb.ring), rb.start))
    if not scheduled:
        raise ValueError(f"{pdf_path}: no all-breed judging found")

    breaks = []
    for ring_no, jname, start in breaks_raw:
        j = judge_of(jname)
        nxt = [rb.start for rb in blocks if judge_of(rb.judge) == j and rb.start > start]
        breaks.append(Break(j, ring_of(ring_no), start, (min(nxt) - start) if nxt else None))

    show = Show(name=show_name, date=day_name, judges=list(judges.values()), events=events,
                rings=sorted(rings.values(), key=lambda r: r.number),
                start=min(sb.start for sb in scheduled), closing_time=closing)
    plan = None
    if groups:
        def resolve(raw: str) -> Judge:
            key = norm_name(raw)
            if key in judges:
                return judges[key]
            last = key.split()[-1]
            same = [j for k, j in judges.items() if k.split()[-1] == last and k[0] == key[0]]
            if len(same) == 1:
                return same[0]
            report.notes.append(f"group/BIS judge not found among breed judges: {raw!r}")
            return judge_of(raw)

        group_judges = {g: resolve(name) for g, name in groups.order}
        show.group_judges = group_judges
        show.bis_judge = resolve(groups.bis) if groups.bis else None
        show.group_rings = tuple(ring_of(n) for n in groups.rings)
        plan = GroupsPlan(show.group_rings, groups.start, tuple(g for g, _ in groups.order),
                          group_judges, show.bis_judge)
        if len(plan.order) != 7:
            report.notes.append(f"groups table lists {len(plan.order)} groups: {plan.order}")
    else:
        report.notes.append("no groups table found")
    judging = {sb.judge for sb in scheduled}
    panel = judging | set(show.group_judges.values()) | ({show.bis_judge} if show.bis_judge else set())
    used = {sb.ring for sb in scheduled} | set(show.group_rings)
    show.judges = [j for j in show.judges if j in judging]
    show.rings = [r for r in show.rings if r in used]
    return ParsedDay(JudgingProgram(show, scheduled, [b for b in breaks if b.judge in panel], plan), report)
