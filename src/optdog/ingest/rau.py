"""Parser for Rau Dog Shows judging programs (raudogshows.com): one show day per file.

Layout (half-letter pages; the ring listings are in two columns, read left then right):

    RING SIX                                  <- a judge's stint in a ring
    JUDGE: Mrs. Betty Nelson Pollock
    1:15 PM                                   <- block "not before" time
    7- Cane Corso 0-3-(2-2)                   <- event: entries, name, breakdown
    1 - Veteran Dog                           <- a class within the breed above (already counted)
    6- *Spaniels (English Springer) 1-1-(2-2) <- '*' is not explained by the program
    12:00 NOON
    LUNCH
    See Ring 5 @ 8:30 AM                      <- where the judge's day continues
    129 Total Dogs                            <- the judge's whole day, at their last stint

A ``... BEGINNER PUPPY ATTRACTION`` title makes the next stint another competition. Junior
Showmanship is listed as ``9- Junior Showmanship Competition`` followed by its classes. The
cover gives the show hours and when breed judging starts; the breed index (ring and time, with
no AM/PM) and the groups table are full-width.

As for every superintendent, every event is read so the counts can be checked, and only the
regular breed judging of the all-breed show becomes domain objects (U 2026-11-13).
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pdfplumber

from optdog.breeds import BreedCatalog, default_catalog
from optdog.domain import (
    Block,
    Break,
    GroupsPlan,
    JudgingEvent,
    JudgingProgram,
    Judge,
    Ring,
    ScheduledBlock,
    Show,
    TimeOfDay,
)
from optdog.ingest.common import (
    DAYS,
    GROUP_WORDS,
    IndexRow,
    ParsedDay,
    ParseReport,
    RawBlock,
    RawEvent,
    cross_check_index,
    parse_counts,
    parse_time,
)
from optdog.ingest.pdftext import extract_columns

NUMBER_WORDS = {w: i for i, w in enumerate(
    "ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN "
    "SIXTEEN SEVENTEEN EIGHTEEN NINETEEN TWENTY".split(), 1)}

RE_RING = re.compile(r"^RING ([A-Z]+)(\s*-\s*NOT IN USE)?$")
RE_JUDGE = re.compile(r"^JUDGE:\s*(.+)$")
RE_SUBTOTAL = re.compile(r"^(\d+) Total Dogs$")
RE_EVENT_COUNTS = re.compile(r"^(\d+)\s*-\s*(\*?)\s*(.+?)\s+(\d+-\d+-\(.*)$")
RE_EVENT = re.compile(r"^(\d+)\s*-\s*(.+)$")
RE_SEE = re.compile(r"^See Ring \d+ @ ")
RE_SWEEPS = re.compile(r"^(Veteran |Puppy )?Sweepstakes$", re.I)  # what follows, until the next time, is sweepstakes
RE_NONREGULAR = re.compile(r"^Veteran ")  # a non-regular class of the breed above: in its entries, not its breakdown
RE_JUNIOR_CLASS = re.compile(r"^JS ")  # a Junior Showmanship class, already in the competition's count
RE_OTHER_TITLE = re.compile(r"BEGINNER PUPPY")  # an uppercase title, not the cover's "Beginner Puppy entries"
RE_GROUPS_TABLE = re.compile(r"\b(Variety|NOHS)\b|^RINGS?$|^RINGS? [A-Z]+ & |Groups and Best in Show|\.{5}|^JUDGES$")
RE_HOURS = re.compile(r"show hours:?\s*([\d:]+\s*[AP]\.?M\.?)\s*to\s*([\d:]+\s*[AP]\.?M\.?)", re.I)
RE_STARTS = re.compile(r"JUDGING OF BREED CLASSES STARTS AT\s+([\d:]+\s*[AP]\.?M\.?)", re.I)
RE_TOTALS = re.compile(r"There are (\d+) dogs entered in this show with a total entry of (\d+) in (\d+) different breeds")
RE_INDEX_ROW = re.compile(r"(\d+)\s+(\D.*?)\s+(\d{1,2})\s+(\d{1,2}):(\d{2})(?=\s|$)")
RE_GROUP_ROW = re.compile(r"^\*?([A-Z][A-Z-]*) GROUP\s*\.+\s*(.+)$")
RE_BIS_ROW = re.compile(r"^BEST IN SHOW\s*\.+\s*(.+)$")
TITLES = re.compile(r"^(Mr|Mrs|Ms|Miss|Dr)\.?\s+", re.I)


def _clock(s: str) -> TimeOfDay | None:
    """'6:30 PM', '1:00 P.M.' -> a time of day."""
    s = re.sub(r"\s*([AP])\.?M\.?$", r" \1M", s.strip(), flags=re.I)
    return parse_time(s)


def _norm(name: str) -> str:
    """A judge's name without title, periods or case, for matching across the program."""
    return " ".join(TITLES.sub("", name.strip().lstrip("*")).replace(".", "").lower().split())


class RauParser:
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
        index = _parse_index(pages)
        groups = _parse_groups(pages)
        pd = self._build(pdf_path, show_name or "Unknown show", day_name, cover, pages, blocks, breaks,
                         subtotals, starred, groups, report)
        if index:
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
        listing = False  # inside the ring listings (not the cover, index, groups table or back matter)
        ring: int | None = None
        judge: str | None = None
        cur: RawBlock | None = None
        cur_time: TimeOfDay | None = None
        other_next = False  # the next stint belongs to another competition
        other = False  # the current stint does
        sweeps = False  # the events since the last time line are sweepstakes
        for col in columns:
            for raw in col.lines:
                line = re.sub(r"\(cid:\d+\)", "", raw).strip()
                if not line:
                    continue
                if m := RE_RING.match(line):
                    listing = True
                    ring = None if m.group(2) else NUMBER_WORDS.get(m.group(1))
                    if ring is None and not m.group(2):
                        report.unparsed.append(f"p{col.page}{col.side}: {line}")
                    judge, cur, cur_time, sweeps = None, None, None, False
                    other, other_next = other_next, False
                    continue
                if RE_OTHER_TITLE.search(line) and line == line.upper():  # e.g. "4-6 MOS BEGINNER PUPPY ATTRACTION"
                    other_next = True
                    continue
                if not listing:
                    continue
                if RE_GROUPS_TABLE.search(line):  # the groups table and back matter, until the next ring
                    listing = False
                    continue
                if m := RE_JUDGE.match(line):
                    judge, cur, cur_time = m.group(1).strip(), None, None
                    continue
                if RE_SEE.match(line) or re.fullmatch(r"\d+", line):
                    continue
                if (t := _clock(line)) is not None:
                    cur_time, cur, sweeps = t, None, False
                    continue
                if RE_SWEEPS.match(line):
                    sweeps, cur = True, None
                    continue
                if line.upper() == "LUNCH":
                    if ring is not None and judge is not None and cur_time is not None:
                        breaks.append((ring, judge, cur_time))
                    cur = None
                    continue
                if m := RE_SUBTOTAL.match(line):
                    if judge is not None:
                        subtotals.append((judge, int(m.group(1))))
                    cur = None
                    continue
                m = RE_EVENT_COUNTS.match(line) or RE_EVENT.match(line)
                if m is None:
                    report.unparsed.append(f"p{col.page}{col.side}: {line}")
                    continue
                entries = int(m.group(1))
                if m.re is RE_EVENT_COUNTS:
                    star, name, counts = m.group(2), m.group(3).strip(), parse_counts(m.group(4))
                else:
                    star, name, counts = "", m.group(2).strip(), None
                if other and cur is not None:
                    continue  # the rest of another competition's listing
                if RE_JUNIOR_CLASS.match(name) or name == "Miscellaneous Classes":
                    continue  # a junior class, or the heading over the Miscellaneous breeds listed below it
                if RE_NONREGULAR.match(name):
                    last = cur.events[-1] if cur is not None and cur.events else None
                    if last is not None and last.counts is not None:
                        last.counts = replace(last.counts, nonregular=last.counts.nonregular + entries)
                    else:
                        report.unparsed.append(f"(non-regular class without a breed) p{col.page}{col.side}: {line}")
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
                breed = not (other or sweeps or name.lower().startswith("junior showmanship"))
                starred += bool(star)
                cur.events.append(RawEvent(name, breed, entries, counts))
        return blocks, breaks, subtotals, starred

    # ---------------------------------------------------------------- domain objects

    def _build(self, pdf_path, show_name, day_name, cover, pages, blocks, breaks_raw, subtotals, starred,
               groups, report: ParseReport) -> ParsedDay:
        judges: dict[str, Judge] = {}

        def judge_of(raw: str) -> Judge:
            key = _norm(raw)
            if key not in judges:
                judges[key] = Judge(name=raw.strip().lstrip("*").strip())
            return judges[key]

        rings: dict[int, Ring] = {}

        def ring_of(n: int) -> Ring:
            return rings.setdefault(n, Ring(n))

        events: list[JudgingEvent] = []
        scheduled: list[ScheduledBlock] = []
        other_competitions = not_breed = 0
        for rb in blocks:
            j = judge_of(rb.judge)
            evs = []
            for raw in rb.events:
                counts = raw.counts
                if counts is not None:
                    # The breakdown gives regular classes and BOB; the non-regular classes are listed
                    # below it (``Veteran Dog``). The entry count agrees with the ring totals and the
                    # index, so it stands; the breakdown is checked against it.
                    if counts.total != raw.entries:
                        report.checks.append((f"breakdown and non-regular classes {raw.name}", raw.entries, counts.total))
                    regular = counts.total - counts.nonregular
                    counts = replace(counts, nonregular=raw.entries - regular) if regular <= raw.entries else None
                if not rb.all_breed:
                    other_competitions += 1
                    continue
                if not raw.breed:
                    not_breed += 1
                    continue
                breed = self.catalog.get(raw.name)
                if breed is None:
                    report.unknown_breeds.append(raw.name)
                evs.append(JudgingEvent(raw.name, j, raw.entries, breed, counts))
            events += evs
            if evs:
                scheduled.append(ScheduledBlock(Block(j, tuple(evs)), ring_of(rb.ring), rb.start))
        if other_competitions:
            report.not_kept.append(f"{other_competitions} events of other competitions on the program (Beginner Puppy)")
        if not_breed:
            report.not_kept.append(f"{not_breed} events of the all-breed show that are not breed judging "
                                   "(sweepstakes, Junior Showmanship)")
        if starred:
            report.notes.append(f"{starred} breeds marked '*', which the program does not explain; judged at the judge's usual pace")
        if not scheduled:
            raise ValueError(f"{pdf_path}: no all-breed judging found")

        breaks = []
        for ring_no, jname, start in breaks_raw:
            j = judge_of(jname)
            nxt = [rb.start for rb in blocks if judge_of(rb.judge) == j and rb.start > start]
            breaks.append(Break(j, ring_of(ring_no), start, (min(nxt) - start) if nxt else None))

        # checks against the program's own figures
        # a judge's "Total Dogs" covers their whole day, wherever it falls in the listing order
        stated_by: dict[str, int] = {}
        for jname, stated in subtotals:
            stated_by[jname] = stated_by.get(jname, 0) + stated
        for jname, stated in stated_by.items():
            parsed = sum(e.entries for rb in blocks if rb.all_breed and rb.judge == jname for e in rb.events)
            report.checks.append((f"{jname} total dogs", stated, parsed))
        start = min(sb.start for sb in scheduled)
        cover_text = "\n".join(pages[:3])
        if m := RE_STARTS.search(cover_text):
            stated_start = _clock(m.group(1))
            report.checks.append(("breed judging start, minutes after midnight", round(stated_start.minutes) if stated_start else -1,
                                  round(start.minutes)))
        if m := RE_TOTALS.search(" ".join(cover_text.split())):
            report.checks.append(("breeds or varieties", int(m.group(3)), len(events)))
            report.notes.append(f"program states {m.group(1)} dogs and a total entry of {m.group(2)}; "
                                f"breed entries parsed: {sum(e.entries for e in events)}")
        closing = None
        if m := RE_HOURS.search(cover):
            closing = _clock(m.group(2))
            report.notes.append(f"show hours {m.group(1)} to {m.group(2)}: the closing time")
        else:
            report.notes.append("no show hours on the cover: no closing time")

        show = Show(name=show_name, date=day_name, judges=list(judges.values()), events=events,
                    rings=sorted(rings.values(), key=lambda r: r.number), start=start, closing_time=closing)
        plan = None
        if groups:
            def resolve(raw: str) -> Judge:
                key = _norm(raw)
                if key in judges:
                    return judges[key]
                last = key.split()[-1]
                same = [j for k, j in judges.items() if k.split()[-1] == last and k[0] == key[0]]
                if len(same) == 1:
                    return same[0]
                report.notes.append(f"group/BIS judge not found among breed judges: {raw!r}")
                return judge_of(raw)

            group_judges = {g: resolve(name) for g, name in groups["order"]}
            show.group_judges = group_judges
            show.bis_judge = resolve(groups["bis"]) if groups["bis"] else None
            show.group_rings = tuple(ring_of(n) for n in groups["rings"])
            plan = GroupsPlan(show.group_rings, groups["start"], tuple(g for g, _ in groups["order"]),
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


# --------------------------------------------------------------------------- full-width pages


def _parse_index(pages: list[str]) -> list[IndexRow]:
    """The 'INDEX OF BREEDS' page: two entries per line, 'ENTRY BREED RING TIME', times without
    AM/PM (before 7 is afternoon)."""
    rows: list[IndexRow] = []
    for text in pages:
        if "INDEX OF BREEDS" not in text:
            continue
        for line in text.splitlines():
            for m in RE_INDEX_ROW.finditer(line):
                if m.group(2).strip() == "Miscellaneous":
                    continue  # one row for the Miscellaneous Class; the listings name its breeds
                h, mm = int(m.group(4)), int(m.group(5))
                if h < 7:
                    h += 12
                rows.append(IndexRow(m.group(2).strip(), int(m.group(1)), TimeOfDay.at(h, mm), int(m.group(3))))
    return rows


def _parse_groups(pages: list[str]) -> dict | None:
    """The Variety Groups table: its rings, start time, order and judges. The NOHS table that
    follows is another competition."""
    for text in pages:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = re.match(r"^([\d:]+\s*[AP]\.?M\.?)\s+Variety Groups", ln, re.I)
            if not m:
                continue
            ring_line = next((x for x in reversed(lines[:i]) if re.match(r"^RINGS? ", x)), "")
            numbers = [NUMBER_WORDS[w] for w in re.findall(r"[A-Z]+", ring_line) if w in NUMBER_WORDS]
            info: dict = {"start": _clock(m.group(1)), "rings": tuple(numbers), "order": [], "bis": None}
            for row in lines[i + 1:]:
                if row.startswith("_") or "NOHS" in row:
                    break
                if gm := RE_GROUP_ROW.match(row):
                    g = GROUP_WORDS.get(gm.group(1).lower())
                    if g is not None and g not in [x for x, _ in info["order"]]:
                        info["order"].append((g, gm.group(2).strip()))
                elif bm := RE_BIS_ROW.match(row):
                    info["bis"] = bm.group(1).strip()
            return info
    return None
