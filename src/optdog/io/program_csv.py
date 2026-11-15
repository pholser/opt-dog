"""Read/write a JudgingProgram as a directory of CSV files.

    show.csv       key,value metadata (name, date, start, group rings/start, BIS judge,
                   ring_sizes such as "3=M 4=S": rings not listed are XL)
    judges.csv     name, permit, minutes_per_dog
    schedule.csv   one row per breed event, in block order: ring, judge, block_start, position,
                   event, breed, group (the breed's, for reading)
    breaks.csv     judge breaks
    groups.csv     group order and judges
    entries.csv    one row per dog, and exhibitors.csv when any is on record: see io/entries_csv.py.
                   Entry counts are counted from the dogs

Only regular breed events of the all-breed show are held (U 2026-11-13).

Times are written as 24-hour HH:MM. CSV is only a serialization of the domain model.
"""

from __future__ import annotations

import csv
from pathlib import Path

from optdog.breeds import BreedCatalog, default_catalog
from optdog.io.entries_csv import check_regular, entries_of, read_entry_table, write_entries
from optdog.domain import (
    Block,
    Break,
    Duration,
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
    TimeOfDay,
)

SCHEDULE_FIELDS = ["ring", "judge", "block_start", "position", "event", "breed", "group"]


def _hhmm(t: TimeOfDay | None) -> str:
    return "" if t is None else t.hhmm()


def _time(s: str) -> TimeOfDay | None:
    return TimeOfDay.parse(s) if s else None


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
            w.writerow([j.name, int(j.permit), "" if j.per_dog is None else j.per_dog.minutes])

    check_regular(e for sb in program.blocks for e in sb.block.events)
    with open(out / "schedule.csv", "w", newline="") as f:
        w = csv.DictWriter(f, SCHEDULE_FIELDS)
        w.writeheader()
        for sb in sorted(program.blocks, key=lambda b: (b.ring.number, b.start)):
            for pos, e in enumerate(sb.block.events):
                w.writerow({
                    "ring": sb.ring.number,
                    "judge": sb.judge.name,
                    "block_start": _hhmm(sb.start),
                    "position": pos,
                    "event": e.name,
                    "breed": e.breed.name if e.breed else "",
                    "group": e.breed.group.value if e.breed and e.breed.group else "",
                })
    write_entries(entries_of(show), out)

    with open(out / "breaks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["judge", "ring", "start", "minutes"])
        for b in sorted(program.breaks, key=lambda b: (b.start, b.judge.name)):
            w.writerow([b.judge.name, b.ring.number if b.ring else "", _hhmm(b.start),
                        "" if b.length is None else f"{b.length.minutes:g}"])

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
            r["name"]: Judge(r["name"], bool(int(r["permit"])),
                             Duration(float(r["minutes_per_dog"])) if r["minutes_per_dog"] else None)
            for r in csv.DictReader(f)
        }
    rings: dict[int, Ring] = {}
    sizes = {int(n): RingSize[s] for n, s in (x.split("=") for x in (meta.get("ring_sizes") or "").split())}

    def ring(n: int) -> Ring:
        return rings.setdefault(n, Ring(n, sizes.get(n, RingSize.XL)))

    table = read_entry_table(d)
    events: list[JudgingEvent] = []
    blocks: dict[tuple[int, str, int], list[JudgingEvent]] = {}
    with open(d / "schedule.csv", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            total, counts = table.counts(r["judge"], r["event"], f"schedule.csv, line {i}")
            e = JudgingEvent(
                name=r["event"],
                kind=EventKind.BREED,
                judge=judges[r["judge"]],
                entries=total,
                breed=catalog[r["breed"]] if r["breed"] else None,
                counts=counts,
            )
            events.append(e)
            blocks.setdefault((int(r["ring"]), r["judge"], _time(r["block_start"])), []).append(e)
    scheduled = [
        ScheduledBlock(Block(judges[j], tuple(evs)), ring(rn), start) for (rn, j, start), evs in blocks.items()
    ]
    breaks = []
    with open(d / "breaks.csv", newline="") as f:
        for r in csv.DictReader(f):
            breaks.append(Break(judges[r["judge"]], ring(int(r["ring"])) if r["ring"] else None, _time(r["start"]),
                                Duration(float(r["minutes"])) if r["minutes"] else None))
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
        start=_time(meta["start"]),
        lunch_window=tuple(_time(t) for t in meta["lunch_window"].split("-")) if meta.get("lunch_window") else None,
        entries=table.attach(events),
    )
    plan = GroupsPlan(group_rings, _time(meta["group_start"]), tuple(order), group_judges, bis) if order else None
    return JudgingProgram(show, scheduled, breaks, plan)
