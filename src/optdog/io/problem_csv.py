"""Read/write a scheduling problem: what a superintendent knows before any program exists.

    show.csv     key,value: name, date, start (HH:MM), lunch_window (HH:MM-HH:MM, optional),
                 bis_judge
    rings.csv    number, size (S/M/L/XL, blank means XL), group (1 for a group ring)
    judges.csv   name, permit, minutes_per_dog (blank: the AKC rate)
    events.csv   one row per thing judged, in running-order preference: judge, event, kind,
                 competition, breed, entries, the five class counts (optional), and pin_ring and
                 pin_start for a fixed commitment (H11). Pinned rows with the same judge,
                 ring and start form one block, in file order. Their ring need not be in rings.csv
                 (obedience and rally run in their own rings)
    groups.csv   group, judge

CSV is only a serialization of the domain model: reading gives a ``Show`` with no schedule.
"""

from __future__ import annotations

import csv
from pathlib import Path

from optdog.breeds import BreedCatalog, default_catalog
from optdog.domain import (
    Block,
    Competition,
    EntryCounts,
    EventKind,
    Group,
    JudgingEvent,
    Judge,
    Ring,
    RingSize,
    ScheduledBlock,
    Show,
)

COUNTS = ("class_dogs", "class_bitches", "bob_dogs", "bob_bitches", "nonregular")
EVENT_FIELDS = ("judge", "event", "kind", "competition", "breed", "entries", *COUNTS, "pin_ring", "pin_start")


def _hhmm(t: int | None) -> str:
    return "" if t is None else f"{t // 60:02d}:{t % 60:02d}"


def _mins(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


class ProblemFormatError(ValueError):
    """The problem's files do not fit together, e.g. an event names a judge not in judges.csv."""


def write_problem(show: Show, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def table(name: str, header, rows) -> None:
        with open(out / name, "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    window = "-".join(_hhmm(t) for t in show.lunch_window) if show.lunch_window else ""
    table("show.csv", ("key", "value"), [("name", show.name), ("date", show.date), ("start", _hhmm(show.start)),
                                         ("lunch_window", window), ("bis_judge", show.bis_judge.name if show.bis_judge else "")])
    group_rings = set(show.group_rings)
    table("rings.csv", ("number", "size", "group"), [(r.number, r.size.name, int(r in group_rings)) for r in show.rings])

    people = {j.name: j for j in show.judges}
    for j in [*show.group_judges.values(), show.bis_judge]:
        if j is not None:
            people.setdefault(j.name, j)
    for e in show.events:
        people.setdefault(e.judge.name, e.judge)
    table("judges.csv", ("name", "permit", "minutes_per_dog"),
          [(j.name, int(j.permit), "" if j.minutes_per_dog is None else j.minutes_per_dog) for j in people.values()])

    pins = {id(e): sb for sb in show.pinned for e in sb.block.events}

    def row(e: JudgingEvent):
        c = e.counts
        sb = pins.get(id(e))
        return (e.judge.name, e.name, e.kind.value, e.competition.value, e.breed.name if e.breed else "", e.entries,
                *("" if c is None else getattr(c, k) for k in COUNTS),
                sb.ring.number if sb else "", _hhmm(sb.start) if sb else "")

    table("events.csv", EVENT_FIELDS, [row(e) for e in show.events])
    table("groups.csv", ("group", "judge"), [(g.value, j.name) for g, j in show.group_judges.items()])


def read_problem(in_dir: str | Path, catalog: BreedCatalog | None = None) -> Show:
    d = Path(in_dir)
    catalog = catalog or default_catalog()

    def rows(name: str) -> list[dict[str, str]]:
        with open(d / name, newline="") as f:
            return list(csv.DictReader(f))

    meta = {r["key"]: r["value"] for r in rows("show.csv")}
    judges = {r["name"]: Judge(r["name"], bool(int(r["permit"] or 0)), float(r["minutes_per_dog"]) if r["minutes_per_dog"] else None)
              for r in rows("judges.csv")}

    def judge(name: str, where: str) -> Judge:
        if name not in judges:
            raise ProblemFormatError(f"{where}: judge {name!r} is not in judges.csv")
        return judges[name]

    rings: list[Ring] = []
    group_rings: list[Ring] = []
    for r in rows("rings.csv"):
        ring = Ring(int(r["number"]), RingSize[r["size"]] if r["size"] else RingSize.XL)
        rings.append(ring)
        if (r.get("group") or "0").strip() == "1":
            group_rings.append(ring)
    by_number = {r.number: r for r in rings}

    events: list[JudgingEvent] = []
    pinned: dict[tuple[str, int, int], list[JudgingEvent]] = {}
    for r in rows("events.csv"):
        where = f"events.csv, {r['event']}"
        counts = EntryCounts(*(int(r[k]) for k in COUNTS)) if r.get("class_dogs") else None
        breed = None
        if r["breed"]:
            breed = catalog.get(r["breed"])
            if breed is None:
                raise ProblemFormatError(f"{where}: breed {r['breed']!r} is not in the AKC catalog")
        e = JudgingEvent(r["event"], EventKind(r["kind"]), judge(r["judge"], where), int(r["entries"]), breed, counts,
                         Competition(r["competition"] or Competition.ALL_BREED.value))
        events.append(e)
        if r.get("pin_ring") or r.get("pin_start"):
            if not (r.get("pin_ring") and r.get("pin_start")):
                raise ProblemFormatError(f"{where}: a pinned event needs both pin_ring and pin_start")
            pinned.setdefault((r["judge"], int(r["pin_ring"]), _mins(r["pin_start"])), []).append(e)

    blocks = [ScheduledBlock(Block(judges[j], tuple(evs)), by_number.get(n, Ring(n)), start)
              for (j, n, start), evs in pinned.items()]
    values = {g.value: g for g in Group}
    group_judges = {}
    for r in rows("groups.csv"):
        if r["group"] not in values:
            raise ProblemFormatError(f"groups.csv: unknown group {r['group']!r}")
        group_judges[values[r["group"]]] = judge(r["judge"], f"groups.csv, {r['group']}")
    bis = judge(meta["bis_judge"], "show.csv, bis_judge") if meta.get("bis_judge") else None
    lunch = tuple(_mins(t) for t in meta["lunch_window"].split("-")) if meta.get("lunch_window") else None
    on_panel = {e.judge.name for e in events}
    return Show(name=meta["name"], date=meta["date"], judges=[j for n, j in judges.items() if n in on_panel], events=events,
                rings=rings, group_judges=group_judges, bis_judge=bis, group_rings=tuple(group_rings),
                start=_mins(meta["start"]), pinned=blocks, lunch_window=lunch)
