"""Parser for MB-F judging programs (infodog.com): one show day per file.

Layout (half-letter pages; the ring listings are in two columns, read left then right):

    RING ONE                                    <- a judge's stint in a ring
    JUDGE: Dr. Andrea Bradford, MD
    8:00 AM                                     <- block "not before" time
    5 American Foxhounds 4-1-(0-0)              <- event: entries, name, breakdown
    15 *Chihuahuas (Long Coat) 7-4-(2-2)        <- '*': the judge is provisional for this breed
    11:45 AM
    Lunch
    1:30 PM
    Junior Showmanship Competition              <- followed by its classes, "1 Novice Junior", ...
    136 Total Dogs                              <- the judge's whole day, other competitions included
    Continuation of assignment from Ring 5 at 2:00 PM
    See Ring 1 at 2:45 PM for balance of assignment.

``CONCURRENT SHOW - <club> 4-6 Months Puppy`` (or ``FSS Open Show``) titles the next stint as
another competition; its events have no breakdown. A stint continued from the previous column
carries on without repeating RING and JUDGE. The breed index (ring and time, with no AM/PM) runs
across the top of the first listing page, and the groups table sits between listings, so neither
ends the listings; the judges' addresses page does. Some programs' fonts print capitals as lower
case ("rING oNe", "JUdGe: dr. lawrence A. letsche, dVM"), so keywords are matched without case and
names are recapitalized.

As for every superintendent, every event is read so the counts can be checked, and only the
regular breed judging of the all-breed show becomes domain objects (U 2026-11-13).
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from optdog.breeds import BreedCatalog, default_catalog
from optdog.domain import TimeOfDay
from optdog.ingest.common import (
    DAYS,
    GROUP_WORDS,
    NUMBER_WORDS,
    GroupsTable,
    IndexRow,
    ParsedDay,
    ParseReport,
    RawBlock,
    RawEvent,
    assemble,
    clock,
    cross_check_index,
    norm_name,
    parse_counts,
)
from optdog.ingest.pdftext import extract_columns

I = re.I
RE_RING = re.compile(r"^RING ([A-Z]+)$", I)
RE_JUDGE = re.compile(r"^JUDGE:\s*(.+)$", I)
RE_SUBTOTAL = re.compile(r"^(\d+) Total Dogs$", I)
RE_EVENT_COUNTS = re.compile(r"^(\d+)\s+(\*?)\s*(\D.*?)\s+(\d+-\d+(?:-.*)?)$")
RE_EVENT = re.compile(r"^(\d+)\s+(\*?)\s*(\D.*)$")
RE_INDEX_LINE = re.compile(r"^\d+\s+\D.*\s\d{1,2}\s+\d{1,2}:\d{2}$")  # a breed-index row: ring and time, no AM/PM
RE_OTHER_TITLE = re.compile(r"^CONCURRENT SHOW\b", I)  # the next stint is another competition
RE_JUNIORS = re.compile(r"^Junior Showmanship Competition$", I)
RE_JUNIOR_CLASS = re.compile(r"^(\d+) (Novice|Open|Master) (Junior|Intermediate|Senior)$", I)
RE_SKIP = re.compile(
    r"^(Continuation of assignment|See Ring \d+ at|Best Junior Handler$|Best in Miscellaneous Class$|"
    r"(Best )?4-6 Month Puppy|Miscellaneous Group$|FSS Group$|Best in Open Show$|"
    r"Entry Breed Ring Time$|\(The groups|\(Immediately|Owner-Handled|[\d:]+ [AP]M Variety)|\.{5}|"
    r"different Breeds or Varieties|"  # the paragraph over the listings, cut by the gutter
    r"(4-6 Months Puppy|FSS Open Show( #\d+)?)$",  # the second line of a CONCURRENT SHOW title
    I,
)
RE_COLUMN_FOOTER = re.compile(r"^(NOTE\b|Emergency Contact|AKC Weekend Club Hotline)", I)  # the rest of the column is not listings
RE_LISTINGS_END = re.compile(r"^(JUDGES \(G# are Group Rings\)|EXHIBITOR INFORMATION|THE AMERICAN KENNEL CLUB IS)", I)
RE_HOURS = re.compile(r"show hours:?\s*([\d:]+\s*[AP]\.?M\.?)\s*to\s*([\d:]+\s*[AP]\.?M\.?)", I)
RE_TOTALS = re.compile(r"There are (\d+) dogs in this show with a total entry of (\d+) in (\d+) different breeds", I)
RE_INDEX_ROW = re.compile(r"(\d+)\s+(\D.*?)\s+(\d{1,2})\s+(\d{1,2}):(\d{2})(?=\s|$)")
RE_GROUPS_START = re.compile(r"^([\d:]+\s*[AP]\.?M\.?)\s+Variety Groups", I)
RE_GROUP_ROW = re.compile(r"^\*?\s*([A-Za-z-]+)\s*\.{3,}\s*\*?\s*(.+)$")
RE_BIS_ROW = re.compile(r"^Best in Show\s*\.{3,}\s*\*?\s*(.+)$", I)

# the 4-6 Month Puppy listing names the Beagle varieties as AKC's standard does; the index and the
# breed listings use the short names
VARIETY_NAMES = {
    "Beagles, Not Exceeding 13 In.": "Beagles, 13 Inch",
    "Beagles, Over 13 In. But Not Exceeding 15 In.": "Beagles, 15 Inch",
}


def recapitalize(name: str) -> str:
    """'dr. lawrence A. letsche, dVM' -> 'Dr. Lawrence A. Letsche, DVM'; a correct name is unchanged."""
    return " ".join(w[:1].upper() + w[1:] for w in name.strip().lstrip("*").strip().split())


class MbfParser:
    def __init__(self, catalog: BreedCatalog | None = None):
        self.catalog = catalog or default_catalog()

    def parse(self, pdf_path: str | Path, show_name: str | None = None) -> list[ParsedDay]:
        pdf_path = Path(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        cover = pages[0]
        day_name = next((d for d in DAYS if re.search(rf"^{d}\b", cover, re.I | re.M)), "UNKNOWN")
        report = ParseReport(source=str(pdf_path), day=day_name)
        blocks, breaks, subtotals, starred = self._scan(extract_columns(pdf_path), report)
        groups = _parse_groups(pages)
        closing = clock(m.group(2)) if (m := RE_HOURS.search(cover)) else None
        pd = assemble(pdf_path, show_name or "Unknown show", day_name, blocks, breaks, groups, closing,
                      self.catalog, report, display_name=recapitalize)
        self._check(pd, pages, cover, blocks, subtotals, starred, report)
        if index := _parse_index(pages):
            cross_check_index(report, blocks, index)
        else:
            report.notes.append("no breed index found; not cross-checked")
        return [pd]

    # ---------------------------------------------------------------- ring listings

    def _scan(self, columns, report: ParseReport):
        blocks: list[RawBlock] = []
        breaks: list[tuple[int, str, TimeOfDay]] = []
        subtotals: list[tuple[str, int]] = []  # judge, stated total for their day
        starred = 0
        listing = False  # inside the ring listings (not the cover, index or back matter)
        ring: int | None = None
        judge: str | None = None
        cur: RawBlock | None = None
        cur_time: TimeOfDay | None = None
        juniors: RawEvent | None = None  # the Junior Showmanship competition whose classes follow
        other_next = False  # the next stint belongs to another competition
        other = False  # the current stint does
        for col in columns:
            for raw in col.lines:
                line = re.sub(r"\(cid:\d+\)", "", raw).strip()
                if not line:
                    continue
                if RE_COLUMN_FOOTER.match(line):
                    break
                if RE_LISTINGS_END.match(line):
                    listing = False
                    continue
                if RE_OTHER_TITLE.match(line):
                    other_next = True
                    continue
                if m := RE_RING.match(line):
                    number = NUMBER_WORDS.get(m.group(1).upper())
                    if number is None:
                        continue  # cut off at the gutter, as over the groups table
                    listing = True
                    ring, judge, cur, cur_time, juniors = number, None, None, None, None
                    other, other_next = other_next, False
                    continue
                if not listing or RE_SKIP.search(line) or RE_INDEX_LINE.match(line) or re.fullmatch(r"\w", line) \
                        or re.fullmatch(r"\d+", line):
                    continue
                if line.lower() == "specialty":
                    # a club's specialty held as the breed's judging at the all-breed show: the breed
                    # is listed once, its BOB goes to the group, and the index and breed count include it
                    report.notes.append(f"a specialty is judged as the all-breed show's breed judging, ring {ring}: kept")
                    continue
                if m := RE_JUDGE.match(line):
                    judge, cur, cur_time, juniors = m.group(1).strip(), None, None, None
                    continue
                if (t := clock(line)) is not None:
                    cur_time, cur, juniors = t, None, None
                    continue
                if line.upper() == "LUNCH":
                    if ring is not None and judge is not None and cur_time is not None:
                        breaks.append((ring, judge, cur_time))
                    cur = None
                    continue
                if m := RE_SUBTOTAL.match(line):
                    if judge is not None:
                        subtotals.append((judge, int(m.group(1))))
                    cur = juniors = None
                    continue
                if m := RE_JUNIOR_CLASS.match(line):
                    if juniors is not None:
                        juniors.entries += int(m.group(1))
                    else:
                        report.unparsed.append(f"(junior class outside a competition) p{col.page}{col.side}: {line}")
                    continue
                is_juniors = bool(RE_JUNIORS.match(line))
                m = None if is_juniors else (RE_EVENT_COUNTS.match(line) or RE_EVENT.match(line))
                if m is None and not is_juniors:
                    report.unparsed.append(f"p{col.page}{col.side}: {line}")
                    continue
                if cur is None and cur_time is None and ring is not None and judge is not None:
                    # a stint continued at the top of a column repeats RING and JUDGE but not the time
                    cur = next((b for b in reversed(blocks) if b.ring == ring and b.judge == judge), None)
                if cur is None and (ring is None or judge is None or cur_time is None):
                    report.unparsed.append(f"(no ring/judge/time) p{col.page}{col.side}: {line}")
                    continue
                if cur is None:
                    assert ring is not None and judge is not None and cur_time is not None
                    cur = RawBlock(ring, judge, cur_time, all_breed=not other)
                    blocks.append(cur)
                if is_juniors:
                    juniors = RawEvent("Junior Showmanship Competition", False, 0)
                    cur.events.append(juniors)
                    continue
                assert m is not None
                counts = parse_counts(m.group(4)) if m.re is RE_EVENT_COUNTS else None
                starred += bool(m.group(2)) and not other
                name = m.group(3).strip()
                cur.events.append(RawEvent(VARIETY_NAMES.get(name, name), not other, int(m.group(1)), counts))
        return blocks, breaks, subtotals, starred

    # ---------------------------------------------------------------- checks

    def _check(self, pd: ParsedDay, pages, cover, blocks, subtotals, starred, report: ParseReport) -> None:
        other_competitions = sum(len(rb.events) for rb in blocks if not rb.all_breed)
        not_breed = sum(1 for rb in blocks if rb.all_breed for e in rb.events if not e.breed)
        if other_competitions:
            report.not_kept.append(f"{other_competitions} events of concurrent competitions (4-6 Month Puppy, FSS open shows)")
        if not_breed:
            report.not_kept.append(f"{not_breed} events of the all-breed show that are not breed judging (Junior Showmanship)")
        if starred:
            report.notes.append(f"{starred} breeds marked '*': the judge is provisional for them (the program's footnote)")
        # a judge's "Total Dogs" covers their whole day, other competitions and juniors included
        stated_by: dict[str, int] = {}
        for jname, stated in subtotals:
            stated_by[norm_name(jname)] = stated_by.get(norm_name(jname), 0) + stated
        names = {norm_name(rb.judge): recapitalize(rb.judge) for rb in blocks}
        for key, stated in stated_by.items():
            parsed = sum(e.entries for rb in blocks if norm_name(rb.judge) == key for e in rb.events)
            report.checks.append((f"{names.get(key, key)} total dogs", stated, parsed))
        show = pd.program.show
        if m := RE_TOTALS.search(" ".join(" ".join(pages[:4]).split())):
            report.checks.append(("breeds or varieties", int(m.group(3)), len(show.events)))
            report.notes.append(f"program states {m.group(1)} dogs and a total entry of {m.group(2)}; "
                                f"breed entries parsed: {sum(e.entries for e in show.events)}")
        if m := RE_HOURS.search(cover):
            report.notes.append(f"show hours {m.group(1)} to {m.group(2)}: the closing time")
        else:
            report.notes.append("no show hours on the cover: no closing time")
        report.notes.append("the program states no breed-judging start; the first block's time is used")


# --------------------------------------------------------------------------- full-width pages


def _parse_index(pages: list[str]) -> list[IndexRow]:
    """The index of breeds, under 'Entry Breed Ring Time' headings: two entries per line, times
    without AM/PM (before 7 is afternoon)."""
    rows: list[IndexRow] = []
    for text in pages:
        if not re.search(r"Entry Breed Ring Time", text, re.I):
            continue
        for line in text.splitlines():
            if re.search(r"There are \d+ dogs", line, re.I):
                break  # the listings start here
            for m in RE_INDEX_ROW.finditer(line):
                h, mm = int(m.group(4)), int(m.group(5))
                if h < 7:
                    h += 12
                rows.append(IndexRow(m.group(2).strip(), int(m.group(1)), TimeOfDay.at(h, mm), int(m.group(3))))
    return rows


def _parse_groups(pages: list[str]) -> GroupsTable | None:
    """The Variety Groups table: its ring, start time, order and judges. The Owner-Handled
    (NOHS) table that follows is another competition."""
    for text in pages:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = RE_GROUPS_START.match(ln)
            if not m:
                continue
            ring_line = next((x for x in reversed(lines[:i]) if re.match(r"^RINGS? ", x, re.I)), "")
            numbers = [NUMBER_WORDS[w] for w in re.findall(r"[A-Z]+", ring_line.upper()) if w in NUMBER_WORDS]
            info = GroupsTable(tuple(numbers), clock(m.group(1)))
            for row in lines[i + 1:]:
                if row.upper().startswith(("OWNER-HANDLED", "RING ")):
                    break
                if bm := RE_BIS_ROW.match(row):
                    info.bis = bm.group(1).strip()
                elif gm := RE_GROUP_ROW.match(row):
                    g = GROUP_WORDS.get(gm.group(1).lower())
                    if g is not None and g not in [x for x, _ in info.order]:
                        info.order.append((g, gm.group(2).strip()))
            return info
    return None
