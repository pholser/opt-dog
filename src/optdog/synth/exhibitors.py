"""Synthetic exhibitors and entries for a parsed show.

Real judging programs give per-breed entry counts but not who shows which dog, so
exhibitor conflicts (SPEC S1) need made-up exhibitors (user decision). The model:

1. Every all-breed breed event's printed breakdown is expanded into dogs: class dogs
   (regular + non-regular classes) and champions (Best of Breed competitors).
2. **Professional handler teams** (about one per ``entries_per_professional`` breed
   entries) show a large share of champions and a smaller share of class dogs. Each
   team has a few "home" groups it mostly works in. A team is the handler plus
   assistants, about one person per ``dogs_per_team_member`` dogs; the person who
   takes a dog in is its exhibitor (user decision). Entries exist before the schedule,
   so a team splits its dogs by breed, not by time: each member takes whole breeds,
   kept together by group. A team member is in conflict only if the schedule puts
   two of their breeds in different rings at once.
3. **Owner-handlers** show the rest: usually one dog, often a few in the same breed
   (breeders), and sometimes a dog in a second breed, which is what creates
   owner conflicts.
4. A share of exhibitors is **registered** for non-conflict accommodation; only
   they count toward S1.

Everything is driven by ``ExhibitorModel`` and a seed, so data is reproducible.
These defaults are guesses, not measurements; they are parameters to be tuned.

    python -m optdog.synth.exhibitors     # writes data/synthetic/<source>/<show>/seed-0/
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from optdog.domain import (
    VARIETY_GROUPS,
    Competition,
    Entry,
    EntryStatus,
    EventKind,
    Exhibitor,
    Group,
    JudgingEvent,
    Show,
)


@dataclass(frozen=True)
class ExhibitorModel:
    seed: int = 0
    entries_per_professional: int = 40  # one professional handler team per this many breed entries
    dogs_per_team_member: int = 12  # a team adds an assistant for about every this many dogs
    pro_share_champion: float = 0.6  # share of champions shown by professionals
    pro_share_class: float = 0.25  # share of class dogs shown by professionals
    pro_home_groups: int = 2  # groups each professional mostly works in
    pro_home_affinity: float = 0.8  # chance a professional's dog comes from a home group
    owner_same_breed_more: float = 0.3  # chance an owner-handler also shows the next dog of the same breed
    owner_second_breed: float = 0.1  # chance a new owner-handler's dog goes to an owner already showing another breed
    owner_second_breed_same_group: float = 0.7  # ...and that other breed is in the same group
    registered_share_owner: float = 0.2
    registered_share_professional: float = 0.5  # handlers and assistants alike


def _dogs(show: Show) -> list[tuple[JudgingEvent, EntryStatus]]:
    out = []
    for e in show.events:
        if e.kind is not EventKind.BREED or e.competition is not Competition.ALL_BREED or e.entries <= 0:
            continue
        if e.counts is None:
            out += [(e, EntryStatus.CLASS)] * e.entries
        else:
            out += [(e, EntryStatus.CLASS)] * e.counts.before_bob
            out += [(e, EntryStatus.CHAMPION)] * e.counts.champions
    return out


def generate(show: Show, model: ExhibitorModel = ExhibitorModel()) -> list[Entry]:
    """Return synthetic entries for ``show`` (does not modify it)."""
    rng = random.Random(model.seed)
    dogs = _dogs(show)
    n_pro = max(1, round(len(dogs) / model.entries_per_professional))
    groups: list[Group] = list(VARIETY_GROUPS)

    teams = [(f"P{i + 1:03d}", set(rng.sample(groups, model.pro_home_groups))) for i in range(n_pro)]

    def pick_team(event: JudgingEvent) -> str:
        home = [t for t, hg in teams if event.group in hg]
        if home and rng.random() < model.pro_home_affinity:
            return rng.choice(home)
        return rng.choice(teams)[0]

    team_dogs: dict[str, list[tuple[str, JudgingEvent, EntryStatus]]] = {t: [] for t, _ in teams}

    owners: list[tuple[Exhibitor, JudgingEvent]] = []  # (owner, an event they show in)
    entries: list[Entry] = []
    last_owner: Exhibitor | None = None
    last_event: JudgingEvent | None = None
    for n, (event, status) in enumerate(dogs, 1):
        dog = f"D{n:05d}"
        share = model.pro_share_champion if status is EntryStatus.CHAMPION else model.pro_share_class
        if rng.random() < share:
            team_dogs[pick_team(event)].append((dog, event, status))
            continue
        if last_owner is not None and last_event is event and rng.random() < model.owner_same_breed_more:
            owner = last_owner  # breeder showing several of the same breed
        elif owners and rng.random() < model.owner_second_breed:
            others = [(o, ev) for o, ev in owners if ev is not event]
            same_group = [(o, ev) for o, ev in others if ev.group is event.group]
            pool = same_group if same_group and rng.random() < model.owner_second_breed_same_group else others
            owner = rng.choice(pool)[0] if pool else None
        else:
            owner = None
        if owner is None:
            owner = Exhibitor(f"O{len(owners) + 1:04d}", registered=rng.random() < model.registered_share_owner)
        owners.append((owner, event))
        entries.append(Entry(dog, event, owner, status))
        last_owner, last_event = owner, event
    entries += _staff_teams(team_dogs, model, rng)
    return entries


def _staff_teams(team_dogs, model: ExhibitorModel, rng: random.Random) -> list[Entry]:
    """Split each team's dogs among the handler and assistants, whole breeds at a time."""
    order = {g: i for i, g in enumerate(VARIETY_GROUPS)}
    out: list[Entry] = []
    for team, dogs in team_dogs.items():
        if not dogs:
            continue
        by_event: dict[int, list] = {}
        for d in dogs:
            by_event.setdefault(id(d[1]), []).append(d)
        events = sorted(by_event.values(), key=lambda ds: (order.get(ds[0][1].group, 99), ds[0][1].name))
        n_members = max(1, -(-len(dogs) // model.dogs_per_team_member))  # ceiling
        members = [
            Exhibitor(team if k == 0 else f"{team}-A{k}", professional=True, team=team,
                      registered=rng.random() < model.registered_share_professional)
            for k in range(n_members)
        ]
        target = len(dogs) / n_members
        k, load = 0, 0
        for ds in events:
            if load >= target and k < n_members - 1:
                k, load = k + 1, 0
            out += [Entry(dog, ev, members[k], st) for dog, ev, st in ds]
            load += len(ds)
    return out


def main() -> None:
    from optdog.io.program_csv import read_program, write_entries

    root = Path(__file__).resolve().parents[3]
    model = ExhibitorModel()
    for d in sorted((root / "data" / "parsed").glob("*/*")):
        if not (d / "schedule.csv").exists():
            continue
        prog = read_program(d)
        entries = generate(prog.show, model)
        out = root / "data" / "synthetic" / d.parent.name / d.name / f"seed-{model.seed}"
        write_entries(entries, out)
        exs = {e.exhibitor for e in entries}
        pros = sum(1 for x in exs if x.professional)
        teams = len({x.team for x in exs if x.team})
        print(f"{d.name:45} {len(entries):5} dogs  {len(exs):5} exhibitors ({teams} handler teams, "
              f"{pros} team members)  {sum(1 for x in exs if x.registered)} registered")


if __name__ == "__main__":
    main()
