"""Parser for Jack Bradshaw Dog Show Superintendents judging programs (jbradshaw.com).

Layout (two-column pages, read left column then right):

    SATURDAY                              <- day header (repeated on each page)
    Total Dogs in Show: 707               <- starts a competition section
    Ring No 1
    Judge: Mr. Jon R. Cole
    Ring Steward: ...
    8:00 AM                               <- block "not before" time
    8 Dogo Argentino 4-2-(1-1)            <- event: entries, name, breakdown
    ...
    11:45 AM
    Lunch
    91 Total Dogs                         <- ring/judge section total
    Judge moves to Ring 3
    ...
    Total Dog Entries in Show: 747        <- ends the section

Special attractions (Beginner Puppy, FSS Open) and concurrent specialties appear as
their own titled sections inside a day. The groups page is full-width and lists the
group start time, rings, order and judges.

The parser builds domain objects (``optdog.domain``). Anything it doesn't understand
goes into ``ParseReport.unparsed`` instead of being dropped silently.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from optdog.breeds import BreedCatalog, default_catalog
from optdog.domain import (
    Block,
    Break,
    Competition,
    EntryCounts,
    EventKind,
    Group,
    GroupsPlan,
    JudgingEvent,
    JudgingProgram,
    Judge,
    Minutes,
    Ring,
    ScheduledBlock,
    Show,
)
from optdog.ingest.pdftext import extract_columns

DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")

RE_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM|A\.M\.|P\.M\.|Noon|NOON)$", re.I)
RE_SECTION_START = re.compile(r"^Total Dogs in Show:\s*(\d+)")
RE_SECTION_END = re.compile(r"^Total Dog Entries in Show:\s*(\d+)")
RE_RING = re.compile(r"^Ring No\.?\s*(\d+)\s*$", re.I)
RE_JUDGE = re.compile(r"^Judge:\s*(.+)$")
RE_SUBTOTAL = re.compile(r"^(?:(\d+) Total Dogs|Total (\d+))\b")
RE_JUNIOR_CLASS = re.compile(r"^\((\d+) ([A-Za-z ]+Class)\)?$")
RE_ARMBANDS = re.compile(r"^\((?:starting|[P\d])")
# counts: 4-2-(1-1) | 16-25-4V-(12-8) | 38-49-1VD-1VB-(13-12) | 0-1 | 4-4-1-(4-3)
RE_ENTRY_COUNTS = re.compile(r"^(\d+)\s+(.+?)\s+(\d+-\d+(?:[-\s(].*)?)$")
RE_ENTRY = re.compile(r"^(\d+)\s+(.+)$")
RE_SWEEPS = re.compile(
    r"\s+(Veteran Sweepstakes?|Vet Sweepstakes|Vet Sweeps|Veteran Sweeps|Veteran Swps|"
    r"Bred by Swps|Sweepstakes|Sweeps|Swps)$",
    re.I,
)
IGNORE = [
    re.compile(p, re.I)
    for p in (
        r"^Ring Steward:",
        r"^(Judges?|Juge|Just) (mo|com|start|will)",
        r"^[\dP,\-\s]+\)$",  # wrapped armband list
        r"^Pee ?Wees?\b",
        r"^Ring reserved for",
        r"^(FSS|Miscellaneous)$",  # category labels inside FSS listings
        r"^\d+$",  # page numbers
        r"^Lunch at the judge",
        r"^Special Attraction$",
    )
]
GROUP_LINE_NAMES = {
    "sporting group": "Sporting Group",
    "hound group": "Hound Group",
    "working group": "Working Group",
    "terrier group": "Terrier Group",
    "toy group": "Toy Group",
    "nonsporting group": "Non-Sporting Group",
    "non-sporting group": "Non-Sporting Group",
    "herding group": "Herding Group",
    "fss group": "FSS Group",
    "best puppy in show": "Best Puppy In Show",
    "herding group, best puppy in show": "Herding Group, Best Puppy In Show",
    "best in miscellaneous": "Best in Miscellaneous",
    "best in miscellaneous class": "Best in Miscellaneous",
    "herding group, bpis": "Herding Group, Best Puppy In Show",
    "bpis": "Best Puppy In Show",
}
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


def parse_time(s: str) -> Minutes | None:
    m = RE_TIME.match(s.strip())
    if not m:
        return None
    h, mm, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3).upper().replace(".", "")
    if ampm == "NOON":
        return 12 * 60 + mm
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return h * 60 + mm


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
    nums = [int(re.match(r"\d+", t).group()) for t in parts if re.match(r"\d+", t)]
    nonreg = sum(nums[2:])
    return EntryCounts(nums[0], nums[1], bob_d, bob_b, nonreg)


# --------------------------------------------------------------------------- report


@dataclass
class ParseReport:
    source: str
    day: str
    unparsed: list[str] = field(default_factory=list)
    unknown_breeds: list[str] = field(default_factory=list)
    checks: list[tuple[str, int, int]] = field(default_factory=list)  # (what, stated, parsed)
    notes: list[str] = field(default_factory=list)
    index_mismatches: list[str] = field(default_factory=list)

    @property
    def failed_checks(self) -> list[tuple[str, int, int]]:
        return [c for c in self.checks if c[1] != c[2]]


@dataclass(frozen=True)
class IndexRow:
    """One line of the program's breed index: 'Golden Retriever......37 9:50 AM 1'."""

    name: str
    entries: int
    time: Minutes
    ring: int


@dataclass
class ParsedDay:
    program: JudgingProgram
    report: ParseReport


# --------------------------------------------------------------------------- internal state


@dataclass
class _RawBlock:
    ring: int
    judge: str
    start: Minutes
    competition: Competition
    events: list[tuple] = field(default_factory=list)  # (name, kind, entries, counts|None)


@dataclass
class _Section:
    title: str
    competition: Competition
    stated_total: int


@dataclass
class _DayState:
    name: str
    sections: list[_Section] = field(default_factory=list)
    blocks: list[_RawBlock] = field(default_factory=list)
    breaks: list[tuple[int, str, Minutes]] = field(default_factory=list)  # ring, judge, start
    subtotals: list[tuple[int, str, int, int, int]] = field(default_factory=list)  # ring, judge, stated, from, to
    unparsed: list[str] = field(default_factory=list)
    section_end_totals: list[tuple[Competition, int]] = field(default_factory=list)


def _subtitle_competition(line: str) -> Competition | None:
    """Sub-titles that introduce a different competition within a day's listing."""
    low = line.lower()
    if "beginner puppy" in low:
        return Competition.BEGINNER_PUPPY
    if re.match(r"^(fss )?open show\b", low):
        return Competition.FSS_OPEN
    if re.search(r"\bclub\b", low) and not re.search(r"\d+-\d+", low):
        return Competition.SPECIALTY
    return None


def _classify_section(title: str) -> Competition:
    for part in title.split(" / "):
        if (sub := _subtitle_competition(part)) is not None:
            return sub
    t = title.lower()
    if "beginner puppy" in t:
        return Competition.BEGINNER_PUPPY
    if "fss" in t:
        return Competition.FSS_OPEN
    if "club" in t or "specialty" in t:
        return Competition.SPECIALTY
    return Competition.ALL_BREED


class BradshawParser:
    def __init__(self, catalog: BreedCatalog | None = None):
        self.catalog = catalog or default_catalog()

    # ---------------------------------------------------------------- public

    def parse(self, pdf_path: str | Path, show_name: str | None = None) -> list[ParsedDay]:
        pdf_path = Path(pdf_path)
        columns = extract_columns(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            full_pages = [p.extract_text() or "" for p in pdf.pages]
        name = show_name or _guess_show_name(full_pages[0])
        dates = _cover_dates(full_pages[0])
        days = self._scan(columns)
        groups = self._parse_groups_pages(full_pages)
        indexes = _parse_breed_indexes(columns)
        out = []
        for day in days:
            pd = self._build_day(pdf_path, name, dates.get(day.name, day.name), day, groups.get(day.name))
            if pd is not None:
                out.append(pd)
        # one breed index per all-breed day, in day order
        if len(indexes) == len(out):
            for pd, idx in zip(out, indexes):
                _cross_check_index(pd, idx)
        else:
            for pd in out:
                pd.report.notes.append(f"found {len(indexes)} breed indexes for {len(out)} all-breed days; not cross-checked")
        return out

    # ---------------------------------------------------------------- scanning

    def _scan(self, columns) -> list[_DayState]:
        days: dict[str, _DayState] = {}
        order: list[str] = []
        day: _DayState | None = None
        in_section = False
        main_competition = Competition.ALL_BREED
        competition = Competition.ALL_BREED
        pending_title: list[str] = []
        ring: int | None = None
        judge: str | None = None
        cur: _RawBlock | None = None
        cur_time: Minutes | None = None
        juniors: list[int] | None = None
        last_event_obedience = False
        section_comp = Competition.ALL_BREED  # competition of the current titled section
        next_ring_comp: Competition | None = None  # set by a sub-title like "Beginner Puppy 4-6 Months"
        seg_start = 0  # index into day.blocks where the current judge's stint in this ring began
        stint_starts: dict[tuple, int] = {}
        section_stated, section_parsed = 0, 0  # entries stated/parsed in the current titled section
        prev_stint_start: int | None = None

        def flush_juniors():
            nonlocal juniors
            if juniors is not None and cur is not None:
                cur.events.append(("Junior Showmanship", EventKind.JUNIOR_SHOWMANSHIP, sum(juniors), None))
            juniors = None

        for col in columns:
            for raw in col.lines:
                line = re.sub(r"\(cid:\d+\)", "", raw).strip()
                if not line:
                    continue
                if line in DAYS:
                    if day is None or day.name != line:
                        flush_juniors()
                        if line not in days:
                            days[line] = _DayState(line)
                            order.append(line)
                        day = days[line]
                        in_section, pending_title, ring, judge, cur = False, [], None, None, None
                    continue
                if day is None:
                    continue
                if m := RE_SECTION_START.match(line):
                    flush_juniors()
                    title = " / ".join(pending_title)
                    competition = next_ring_comp or _classify_section(title)
                    if not day.sections:
                        main_competition = competition
                    section_comp = competition
                    section_stated, section_parsed = int(m.group(1)), 0
                    day.sections.append(_Section(title, competition, int(m.group(1))))
                    in_section, pending_title, ring, judge, cur = True, [], None, None, None
                    next_ring_comp = None
                    continue
                if m := RE_SECTION_END.match(line):
                    flush_juniors()
                    if in_section:  # ignore totals in back matter such as the breed index
                        day.section_end_totals.append((section_comp, int(m.group(1))))
                    in_section, cur = False, None
                    competition = section_comp = main_competition
                    continue
                if m := RE_RING.match(line):
                    flush_juniors()
                    if not in_section:  # rings after a sub-section ended resume the main show
                        section_comp = main_competition
                        in_section = True
                    competition = next_ring_comp or section_comp
                    next_ring_comp = None
                    ring, judge, cur, cur_time = int(m.group(1)), None, None, None
                    continue
                if not in_section:
                    pending_title.append(line)
                    if len(pending_title) > 4:
                        pending_title = pending_title[-4:]
                    continue
                if m := RE_JUDGE.match(line):
                    flush_juniors()
                    judge, cur, cur_time = m.group(1).strip(), None, None
                    prev_stint_start = stint_starts.get((ring, judge))
                    seg_start = stint_starts[(ring, judge)] = len(day.blocks)
                    continue
                if line == "(continued)":  # block carried over from the previous column/page
                    prev = [b for b in day.blocks if b.ring == ring and b.judge == judge]
                    if prev:
                        cur, cur_time = prev[-1], prev[-1].start
                    if prev_stint_start is not None:
                        seg_start = stint_starts[(ring, judge)] = prev_stint_start
                    continue
                if any(p.search(line) for p in IGNORE):
                    continue
                if (t := parse_time(line)) is not None:
                    flush_juniors()
                    cur_time = t
                    cur = None
                    continue
                if line.lower() == "lunch":
                    flush_juniors()
                    if ring is not None and judge is not None and cur_time is not None:
                        day.breaks.append((ring, judge, cur_time))
                    cur = None
                    continue
                if m := RE_SUBTOTAL.match(line):
                    flush_juniors()
                    stated = int(m.group(1) or m.group(2))
                    if ring is not None and judge is not None:
                        day.subtotals.append((ring, judge, stated, seg_start, len(day.blocks)))
                    seg_start = len(day.blocks)
                    stint_starts.pop((ring, judge), None)
                    cur = None
                    # A sub-title applies to one ring stint. A titled sub-section (own "Total Dogs
                    # in Show") lasts until its "Total Dog Entries" line or until its stated
                    # number of dogs has been listed (some programs omit the end line).
                    competition = section_comp
                    if section_comp is not main_competition and section_parsed >= section_stated:
                        section_comp = competition = main_competition
                    continue
                if line.startswith("Junior Showmanship Competition"):
                    flush_juniors()
                    cur = self._open_block(day, ring, judge, cur_time, competition, line)
                    juniors = []
                    continue
                if m := RE_JUNIOR_CLASS.match(line):
                    if juniors is None:  # juniors listed without a "Competition" heading
                        cur = cur or self._open_block(day, ring, judge, cur_time, competition, line)
                        juniors = []
                    juniors.append(int(m.group(1)))
                    if competition is section_comp:
                        section_parsed += int(m.group(1))
                    continue
                if juniors is not None and line in ("Class)",):
                    continue
                if last_event_obedience and RE_ARMBANDS.match(line):
                    continue
                gl = GROUP_LINE_NAMES.get(line.lower())
                if gl is not None:
                    flush_juniors()
                    cur = cur or self._open_block(day, ring, judge, cur_time, competition, line)
                    if cur is not None:
                        cur.events.append((gl, EventKind.GROUP, 0, None))
                    continue
                if (m := RE_ENTRY_COUNTS.match(line)) or (m := RE_ENTRY.match(line)):
                    entries, name = int(m.group(1)), m.group(2)
                    counts = parse_counts(m.group(3)) if m.re is RE_ENTRY_COUNTS else None
                    flush_juniors()
                    cur = cur or self._open_block(day, ring, judge, cur_time, competition, line)
                    if cur is None:
                        continue
                    kind = EventKind.BREED
                    low = name.lower()
                    last_event_obedience = False
                    if low.startswith(("obedience", "rally")) or re.match(
                        r"^(beginner novice|novice class|open class|utility class|open graduate|"
                        r"graduate novice|preferred|versatility)",
                        low,
                    ):
                        kind = EventKind.OTHER
                        last_event_obedience = True
                    elif RE_SWEEPS.search(name) or "sweep" in low or " swps" in low:
                        kind = EventKind.SWEEPSTAKES
                    elif re.search(r"\b(head type|movement)$", low):
                        kind = EventKind.OTHER
                    cur.events.append((name, kind, entries, counts))
                    if competition is section_comp:
                        section_parsed += entries
                    continue
                if (sub := _subtitle_competition(line)) is not None:
                    next_ring_comp = sub
                    continue
                day.unparsed.append(f"p{col.page}{col.side}: {line}")
        flush_juniors()
        return [days[d] for d in order]

    def _open_block(self, day, ring, judge, t, competition, line) -> _RawBlock | None:
        if ring is None or judge is None or t is None:
            day.unparsed.append(f"(no ring/judge/time) {line}")
            return None
        b = _RawBlock(ring, judge, t, competition)
        day.blocks.append(b)
        return b

    # ---------------------------------------------------------------- groups page

    def _parse_groups_pages(self, pages: list[str]) -> dict[str, dict]:
        """Return {day: {"start", "rings", "order": [(Group, judge_str)], "bis": judge_str}}."""
        out: dict[str, dict] = {}
        for text in pages:
            if "Group" not in text or not re.search(r"Group(s| Judging) will start", text):
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            day = next((ln for ln in lines[:3] if ln in DAYS), None)
            if day is None:
                continue
            # header(s): "Variety Group Judging will start at 2 PM in Rings 5 and 7."
            #            "NOHS Groups will start at 2 pm in Rings 1-2 Variety Groups will start at 2:30 pm in Rings 4-5"
            info: dict = {"start": None, "rings": (), "order": [], "bis": None, "raw": []}
            columns_order: list[str] = []
            for ln in lines:
                for m in re.finditer(
                    r"(Variety|NOHS)\s+Group(?:s| Judging)\s+will start\s+at\s+([\d:]+\s*[ap]\.?m\.?)\s+in\s+Rings?\s+([\d\s,\-and]+\d)",
                    ln,
                    re.I,
                ):
                    columns_order.append(m.group(1).lower())
                    if m.group(1).lower() == "variety":
                        info["start"] = parse_time(m.group(2).replace(" ", " ").upper().replace("PM", " PM").replace("AM", " AM").replace("  ", " "))
                        info["rings"] = tuple(int(x) for x in re.findall(r"\d+", m.group(3)))
                        if "-" in m.group(3):
                            a, b = info["rings"][:2]
                            info["rings"] = tuple(range(a, b + 1))
                if re.search(r"NOHS Group Judging (immediately|will start immediately)", ln, re.I):
                    if "variety" in columns_order or not columns_order:
                        columns_order = (columns_order or ["variety"]) + ["nohs"]
            variety_col = columns_order.index("variety") if "variety" in columns_order else 0
            side_by_side = len(columns_order) >= 2 and any(
                len(_split_group_pairs(ln)) == 2 for ln in lines
            )
            section = "variety" if not columns_order else columns_order[0]
            for ln in lines:
                if re.search(r"Variety Group", ln) and not side_by_side:
                    section = "variety"
                    continue
                if re.search(r"NOHS Group", ln) and not side_by_side:
                    section = "nohs"
                    continue
                pairs = _split_group_pairs(ln)
                if not pairs:
                    continue
                if side_by_side and len(pairs) == 2:
                    label, judge = pairs[variety_col]
                elif section == "variety" or side_by_side:
                    label, judge = pairs[0]
                else:
                    continue
                if label == "BIS":
                    info["bis"] = judge
                elif label in GROUP_WORDS and GROUP_WORDS[label] not in [g for g, _ in info["order"]]:
                    info["order"].append((GROUP_WORDS[label], judge))
            out[day] = info
        return out

    # ---------------------------------------------------------------- building domain objects

    def _build_day(self, pdf_path, show_name, date, day: _DayState, groups_info) -> ParsedDay | None:
        report = ParseReport(source=str(pdf_path), day=day.name, unparsed=list(day.unparsed))
        main = next((s for s in day.sections if s.competition is Competition.ALL_BREED), None)
        breed_blocks = [b for b in day.blocks if b.competition is Competition.ALL_BREED]
        if main is None or not any(k is EventKind.BREED for b in breed_blocks for _, k, _, _ in b.events):
            return None  # no all-breed conformation show this day

        judges: dict[str, Judge] = {}

        def judge_of(raw: str) -> Judge:
            permit = raw.startswith("*")
            name = raw.lstrip("*").strip()
            if name not in judges:
                judges[name] = Judge(name=name, permit=permit)
            return judges[name]

        rings: dict[int, Ring] = {}

        def ring_of(n: int) -> Ring:
            return rings.setdefault(n, Ring(n))

        events: list[JudgingEvent] = []
        scheduled: list[ScheduledBlock] = []
        for rb in day.blocks:
            j = judge_of(rb.judge)
            evs = []
            for name, kind, entries, counts in rb.events:
                breed = None
                if kind is EventKind.BREED:
                    breed = self.catalog.get(name)
                    if breed is None:
                        report.unknown_breeds.append(name)
                elif kind is EventKind.SWEEPSTAKES:
                    breed = self.catalog.get(RE_SWEEPS.sub("", name))
                comp = rb.competition
                if kind is EventKind.OTHER and name.lower().startswith(("obedience-rally", "rally")):
                    comp = Competition.RALLY
                elif kind is EventKind.OTHER:
                    comp = Competition.OBEDIENCE
                ev = JudgingEvent(name, kind, j, entries, breed, counts, comp)
                evs.append(ev)
                events.append(ev)
                if counts is not None and counts.total != entries:
                    report.checks.append((f"breakdown {name}", entries, counts.total))
            if evs:
                scheduled.append(ScheduledBlock(Block(j, tuple(evs)), ring_of(rb.ring), rb.start))

        # breaks: length = gap until that judge's next block in the same ring, if any
        breaks = []
        for ring_no, jname, start in day.breaks:
            j = judge_of(jname)
            nxt = [sb.start for sb in scheduled if sb.judge == j and sb.start > start]
            minutes = (min(nxt) - start) if nxt else None
            breaks.append(Break(j, ring_of(ring_no), start, minutes))

        # totals checks
        for ring_no, jname, stated, lo, hi in day.subtotals:
            parsed = sum(
                entries
                for rb in day.blocks[lo:hi]
                if rb.ring == ring_no and rb.judge == jname
                for _, kind, entries, _ in rb.events
            )
            report.checks.append((f"ring {ring_no} {jname} subtotal", stated, parsed))
        main_entries = sum(
            e.entries
            for e in events
            if e.competition in (Competition.ALL_BREED, Competition.OBEDIENCE, Competition.RALLY)
        )
        ends = [n for comp, n in day.section_end_totals if comp is Competition.ALL_BREED]
        if ends:
            report.checks.append(("Total Dog Entries in Show (all-breed section)", ends[-1], main_entries))
        else:
            report.notes.append("no 'Total Dog Entries in Show' line for the all-breed section")
        breed_entries = sum(e.entries for e in events if e.competition is Competition.ALL_BREED and e.kind is EventKind.BREED)
        report.notes.append(f"Total Dogs in Show (unique dogs, informational): {main.stated_total}; breed entries parsed: {breed_entries}")

        show = Show(
            name=show_name,
            date=date,
            judges=list(judges.values()),
            events=events,
            rings=sorted(rings.values(), key=lambda r: r.number),
        )
        plan = None
        if groups_info:
            group_judges = {}
            for g, raw in groups_info["order"]:
                j = _resolve_judge(raw, show.judges, report)
                if j is not None:
                    group_judges[g] = j
            bis = _resolve_judge(groups_info["bis"], show.judges, report) if groups_info["bis"] else None
            show.group_judges = group_judges
            show.bis_judge = bis
            show.group_rings = tuple(ring_of(n) for n in groups_info["rings"])
            plan = GroupsPlan(
                rings=show.group_rings,
                start=groups_info["start"],
                order=tuple(g for g, _ in groups_info["order"]),
                judges=group_judges,
                bis_judge=bis,
            )
            if len(plan.order) != 7:
                report.notes.append(f"groups page lists {len(plan.order)} groups: {plan.order}")
        else:
            report.notes.append("no groups page found for this day")
        starts = [sb.start for sb in scheduled if sb.block.events[0].competition is Competition.ALL_BREED]
        show.start = min(starts) if starts else None
        return ParsedDay(JudgingProgram(show, scheduled, breaks, plan), report)


# --------------------------------------------------------------------------- helpers


def _split_group_pairs(line: str) -> list[tuple[str, str]]:
    """'Herding Mr. Murphy Herding Mr. Newton' -> [('herding','Mr. Murphy'), ('herding','Mr. Newton')].
    'Best In Show Miss Lex' -> [('BIS', 'Miss Lex')]."""
    labels = r"(Sporting|Hounds?|Working|Terriers?|Toys?|Non-?Sporting|Herding|Best In Show|BISOH)"
    parts = re.split(rf"(?:^|\s){labels}\s*(?:[–-]\s*)?(?=(?:Mr|Mrs|Ms|Miss|Dr)\.?)", line, flags=re.I)
    if len(parts) < 3:
        m = re.match(r"^(Best In Show|BISOH)\s+([A-Z][\w.\-' ]+)$", line.strip(), re.I)
        if m:  # judge printed without a title, e.g. "Best In Show Queijeiro"
            return [("BIS" if m.group(1).lower() == "best in show" else "BISOH", m.group(2).strip())]
        return []
    out = []
    for i in range(1, len(parts) - 1, 2):
        label = parts[i].lower()
        judge = parts[i + 1].strip()
        if label == "best in show":
            label = "BIS"
        elif label == "bisoh":
            label = "BISOH"
        out.append((label, judge))
    return out


_TITLE_GENDER = {"mr": "m", "mrs": "f", "ms": "f", "miss": "f"}


def _resolve_judge(raw: str, judges: list[Judge], report: ParseReport) -> Judge | None:
    """Match an abbreviated name like 'Mr. Murphy' or 'Mr.Horn' to a full judge name."""
    m = re.match(r"^(Mr|Mrs|Ms|Miss|Dr)\.?\s*(.+)$", raw.strip().rstrip("*").strip(), re.I)
    title, surname = (m.group(1).lower(), m.group(2).strip()) if m else (None, raw.strip())
    surname_l = surname.lower()

    def jtitle(j):
        t = j.name.split()[0].rstrip(".").lower()
        return t

    def last(j):
        return j.name.rstrip(",").split()[-1].lower().rstrip(",")

    scored = []
    for j in judges:
        exact = last(j) == surname_l
        partial = surname_l in j.name.lower()
        if not (exact or partial):
            continue
        g_ok = title is None or _TITLE_GENDER.get(title) is None or _TITLE_GENDER.get(title) == _TITLE_GENDER.get(jtitle(j))
        scored.append(((exact, title == jtitle(j), g_ok), j))
    if not scored:
        report.notes.append(f"group/BIS judge not found among breed judges: {raw!r}")
        return Judge(raw.strip().rstrip("*").strip())  # group-only judge, not on the breed panel this day
    scored.sort(key=lambda s: s[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        report.notes.append(f"ambiguous judge {raw!r}: {[s[1].name for s in scored]}")
    if not scored[0][0][2]:
        report.notes.append(f"judge {raw!r} matched {scored[0][1].name} despite title mismatch")
    return scored[0][1]


def _guess_show_name(cover: str) -> str:
    for ln in cover.splitlines():
        s = ln.strip()
        if re.search(r"kennel club", s, re.I) and not re.search(r"american kennel club", s, re.I):
            return s.title()
    return "Unknown show"


def _cover_dates(cover: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+ \d{1,2}, \d{4})", cover):
        out.setdefault(m.group(1).upper(), f"{m.group(1)}, {m.group(2)}")
    return out


RE_INDEX = re.compile(
    r"^(?P<name>.*?[A-Za-z)])\.*\s*(?P<n>\d+)\s+(?P<time>\d{1,2}:\d{2}\s*[AP]M|12\s*Noon)\s+(?P<ring>\d+)$", re.I
)


def _parse_breed_indexes(columns) -> list[list[IndexRow]]:
    """Each 'BREED TOTAL TIME NO' heading starts an index; rows follow until the next heading."""
    out: list[list[IndexRow]] = []
    cur: list[IndexRow] | None = None
    for col in columns:
        for raw in col.lines:
            line = raw.strip()
            if line.startswith("BREED TOTAL TIME"):
                cur = []
                out.append(cur)
                continue
            if cur is None:
                continue
            m = RE_INDEX.match(line)
            if m:
                cur.append(IndexRow(m.group("name").strip(), int(m.group("n")), parse_time(m.group("time")), int(m.group("ring"))))
    return [ix for ix in out if ix]


def _cross_check_index(pd: ParsedDay, index: list[IndexRow]) -> None:
    """Compare the back-of-program breed index with the parsed ring listings."""
    from optdog.breeds import name_key

    rows = defaultdict(list)
    for e, sb, _ in pd.program.scheduled_events():
        rows[name_key(e.name)].append((e, sb))
    report = pd.report
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
        ok = [
            (e, sb)
            for e, sb in cands
            if e.entries == row.entries and sb.start == row.time and sb.ring.number == row.ring
        ]
        if ok:
            matched += 1
        else:
            e, sb = cands[0]
            report.index_mismatches.append(
                f"{row.name!r}: index {row.entries} @ {row.time // 60}:{row.time % 60:02d} ring {row.ring}; "
                f"listing {e.entries} @ {sb.start // 60}:{sb.start % 60:02d} ring {sb.ring.number}"
            )
    report.checks.append(("breed index rows (breeds) matching listings", len(index), matched))
