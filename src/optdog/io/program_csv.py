"""Read/write a JudgingProgram as a directory of CSV files.

    show.csv       key,value metadata (name, date, start, group rings/start, BIS judge,
                   ring_sizes such as "3=M 4=S": rings not listed are XL)
    judges.csv     name, permit, minutes_per_dog
    schedule.csv   one row per event, in block order (ring, judge, block_start, position, ...)
    breaks.csv     judge breaks
    groups.csv     group order and judges

Times are written as 24-hour HH:MM. CSV is only a serialization of the domain model.
"""

from __future__ import annotations

import csv
from pathlib import Path

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
    Ring,
    RingSize,
    ScheduledBlock,
    Show,
)

SCHEDULE_FIELDS = [
    "ring", "judge", "block_start", "position", "event", "kind", "competition", "breed", "group",
    "entries", "class_dogs", "class_bitches", "bob_dogs", "bob_bitches", "nonregular",
]


def _hhmm(t: int | None) -> str:
    return "" if t is None else f"{t // 60:02d}:{t % 60:02d}"


def _mins(s: str) -> int | None:
    if not s:
        return None
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def write_program(program: JudgingProgram, out_dir: str | Path, source: str = "") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    show, plan = program.show, program.groups
    meta = {
        "name": show.name,
        "date": show.date,
        "source": source,
        "start": _hhmm(show.start),
        "group_rings": " ".join(str(r.number) for r in show.group_rings),
        "ring_sizes": " ".join(f"{r.number}={r.size.name}" for r in show.rings if r.size is not RingSize.XL),
        "group_start": _hhmm(plan.start if plan else None),
        "lunch_window": "-".join(_hhmm(t) for t in show.lunch_window) if show.lunch_window else "",
        "bis_judge": show.bis_judge.name if show.bis_judge else "",
    }
    with open(out / "show.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "value"])
        w.writerows(meta.items())

    judges = {j.name: j for j in show.judges}
    for j in list(show.group_judges.values()) + ([show.bis_judge] if show.bis_judge else []):
        judges.setdefault(j.name, j)
    with open(out / "judges.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "permit", "minutes_per_dog"])
        for j in judges.values():
            w.writerow([j.name, int(j.permit), "" if j.minutes_per_dog is None else j.minutes_per_dog])

    with open(out / "schedule.csv", "w", newline="") as f:
        w = csv.DictWriter(f, SCHEDULE_FIELDS)
        w.writeheader()
        for sb in sorted(program.blocks, key=lambda b: (b.ring.number, b.start)):
            for pos, e in enumerate(sb.block.events):
                c = e.counts
                w.writerow({
                    "ring": sb.ring.number,
                    "judge": sb.judge.name,
                    "block_start": _hhmm(sb.start),
                    "position": pos,
                    "event": e.name,
                    "kind": e.kind.value,
                    "competition": e.competition.value,
                    "breed": e.breed.name if e.breed else "",
                    "group": e.breed.group.value if e.breed and e.breed.group else "",
                    "entries": e.entries,
                    "class_dogs": c.class_dogs if c else "",
                    "class_bitches": c.class_bitches if c else "",
                    "bob_dogs": c.bob_dogs if c else "",
                    "bob_bitches": c.bob_bitches if c else "",
                    "nonregular": c.nonregular if c else "",
                })

    with open(out / "breaks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["judge", "ring", "start", "minutes"])
        for b in sorted(program.breaks, key=lambda b: (b.start, b.judge.name)):
            w.writerow([b.judge.name, b.ring.number if b.ring else "", _hhmm(b.start), "" if b.minutes is None else b.minutes])

    with open(out / "groups.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order", "group", "judge"])
        if plan:
            for i, g in enumerate(plan.order, 1):
                j = plan.judges.get(g)
                w.writerow([i, g.value, j.name if j else ""])


def read_program(in_dir: str | Path, catalog: BreedCatalog | None = None) -> JudgingProgram:
    d = Path(in_dir)
    catalog = catalog or default_catalog()
    with open(d / "show.csv", newline="") as f:
        meta = {r["key"]: r["value"] for r in csv.DictReader(f)}
    with open(d / "judges.csv", newline="") as f:
        judges = {
            r["name"]: Judge(r["name"], bool(int(r["permit"])), float(r["minutes_per_dog"]) if r["minutes_per_dog"] else None)
            for r in csv.DictReader(f)
        }
    rings: dict[int, Ring] = {}
    sizes = {int(n): RingSize[s] for n, s in (x.split("=") for x in (meta.get("ring_sizes") or "").split())}

    def ring(n: int) -> Ring:
        return rings.setdefault(n, Ring(n, sizes.get(n, RingSize.XL)))

    events: list[JudgingEvent] = []
    blocks: dict[tuple[int, str, int], list[JudgingEvent]] = {}
    with open(d / "schedule.csv", newline="") as f:
        for r in csv.DictReader(f):
            counts = None
            if r["class_dogs"] != "":
                counts = EntryCounts(*(int(r[k]) for k in ("class_dogs", "class_bitches", "bob_dogs", "bob_bitches", "nonregular")))
            e = JudgingEvent(
                name=r["event"],
                kind=EventKind(r["kind"]),
                judge=judges[r["judge"]],
                entries=int(r["entries"]),
                breed=catalog[r["breed"]] if r["breed"] else None,
                counts=counts,
                competition=Competition(r["competition"]),
            )
            events.append(e)
            blocks.setdefault((int(r["ring"]), r["judge"], _mins(r["block_start"])), []).append(e)
    scheduled = [
        ScheduledBlock(Block(judges[j], tuple(evs)), ring(rn), start) for (rn, j, start), evs in blocks.items()
    ]
    breaks = []
    with open(d / "breaks.csv", newline="") as f:
        for r in csv.DictReader(f):
            breaks.append(Break(judges[r["judge"]], ring(int(r["ring"])) if r["ring"] else None, _mins(r["start"]),
                                int(r["minutes"]) if r["minutes"] else None))
    groups_by_value = {g.value: g for g in Group}
    order, group_judges = [], {}
    with open(d / "groups.csv", newline="") as f:
        for r in csv.DictReader(f):
            g = groups_by_value[r["group"]]
            order.append(g)
            if r["judge"]:
                group_judges[g] = judges[r["judge"]]
    group_rings = tuple(ring(int(x)) for x in meta["group_rings"].split())
    bis = judges.get(meta["bis_judge"]) if meta["bis_judge"] else None
    breed_judges = {e.judge.name for e in events}
    show = Show(
        name=meta["name"],
        date=meta["date"],
        judges=[j for n, j in judges.items() if n in breed_judges],
        events=events,
        rings=sorted(rings.values(), key=lambda r: r.number),
        group_judges=group_judges,
        bis_judge=bis,
        group_rings=group_rings,
        start=_mins(meta["start"]),
        lunch_window=tuple(_mins(t) for t in meta["lunch_window"].split("-")) if meta.get("lunch_window") else None,
    )
    plan = GroupsPlan(group_rings, _mins(meta["group_start"]), tuple(order), group_judges, bis) if order else None
    return JudgingProgram(show, scheduled, breaks, plan)


# --------------------------------------------------------------------------- entries / exhibitors
#
#     exhibitors.csv  id, professional, registered, team
#     entries.csv     dog, exhibitor, status, judge, event, competition
#
# An entry refers to its event by (judge, event name, competition), unique within a show.


def write_entries(entries, out_dir: str | Path) -> None:
    from optdog.domain import Entry  # noqa: F401  (type context)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    exhibitors = {}
    for en in entries:
        exhibitors.setdefault(en.exhibitor.id, en.exhibitor)
    with open(out / "exhibitors.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "professional", "registered", "team"])
        for x in exhibitors.values():
            w.writerow([x.id, int(x.professional), int(x.registered), x.team or ""])
    with open(out / "entries.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dog", "exhibitor", "status", "judge", "event", "competition"])
        for en in entries:
            w.writerow([en.dog, en.exhibitor.id, en.status.value, en.event.judge.name, en.event.name, en.event.competition.value])


def read_entries(in_dir: str | Path, program: JudgingProgram) -> list:
    """Read entries written by ``write_entries``, attached to ``program``'s event objects."""
    return read_entries_for(in_dir, program.show.events)


def read_entries_for(in_dir: str | Path, events) -> list:
    """Entries from ``exhibitors.csv`` and ``entries.csv``, attached to the given event objects.

    One exhibitor per person who takes dogs into the ring: each member of a professional
    handler's team is an exhibitor of their own, and ``team`` only labels them. Raises
    ``ValueError`` naming the file and row for a bad reference or value.
    """
    from optdog.domain import Entry, EntryStatus, Exhibitor

    d = Path(in_dir)

    def flag(value: str, where: str) -> bool:
        v = (value or "0").strip()
        if v not in ("0", "1"):
            raise ValueError(f"{where}: expected 0 or 1, got {value!r}")
        return v == "1"

    exhibitors: dict[str, Exhibitor] = {}
    with open(d / "exhibitors.csv", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            where = f"exhibitors.csv, line {i}"
            if not r.get("id"):
                raise ValueError(f"{where}: no id")
            if r["id"] in exhibitors:
                raise ValueError(f"{where}: exhibitor {r['id']!r} is listed twice")
            exhibitors[r["id"]] = Exhibitor(r["id"], flag(r.get("professional"), where), flag(r.get("registered"), where),
                                            r.get("team") or None)
    by_key: dict[tuple[str, str, str], list] = {}
    for e in events:
        by_key.setdefault((e.judge.name, e.name, e.competition.value), []).append(e)
    statuses = {s.value for s in EntryStatus}
    out = []
    with open(d / "entries.csv", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            where = f"entries.csv, line {i}"
            key = (r["judge"], r["event"], r.get("competition") or "all-breed")
            evs = by_key.get(key)
            if not evs:
                raise ValueError(f"{where}: no event {r['event']!r} judged by {r['judge']!r} ({key[2]})")
            if len(evs) > 1:
                raise ValueError(f"{where}: event {r['event']!r} for {r['judge']!r} ({key[2]}) is listed more than once")
            if r["exhibitor"] not in exhibitors:
                raise ValueError(f"{where}: exhibitor {r['exhibitor']!r} is not in exhibitors.csv")
            if r["status"] not in statuses:
                raise ValueError(f"{where}: status {r['status']!r} is not one of {sorted(statuses)}")
            out.append(Entry(r["dog"], evs[0], exhibitors[r["exhibitor"]], EntryStatus(r["status"])))
    return out
