"""Solved schedules on disk, so reports and figures read results instead of re-solving.

    data/solved/<source>/<show>/bound.json              the relaxation's lower bound
    data/solved/<source>/<show>/<step>/*.csv            the schedule (see io/program_csv.py)
    data/solved/<source>/<show>/<step>/solve.json       finish, proven bound, phases, costs

``python -m optdog.analysis.solve_all`` writes them. Loading matches each saved event back
to the show's own objects, so the validator sees the same instance the solver did, pinned
blocks included.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from optdog.domain import Block, Break, GroupsPlan, JudgingEvent, JudgingProgram, ScheduledBlock, Show
from optdog.instance import instance_from_program
from optdog.io.program_csv import read_entries, read_program, write_program
from optdog.solve.cpsat import PhaseStats, PlacementResult
from optdog.solve.relaxation import RelaxedBound

ROOT = Path(__file__).resolve().parents[2]
PARSED = ROOT / "data" / "parsed"
SYNTH = ROOT / "data" / "synthetic"
SOLVED = ROOT / "data" / "solved"
STEPS = ("placement", "formation", "ordering")


def _event_key(e: JudgingEvent) -> tuple[str, str, str]:
    return e.judge.name, e.name, e.competition.value


def _pin_key(sb: ScheduledBlock) -> tuple:
    return sb.ring.number, sb.start, sb.judge.name, tuple(e.name for e in sb.block.events)


def save_result(result: PlacementResult, out_dir: Path) -> None:
    write_program(result.program, out_dir, source="optdog")
    (out_dir / "solve.json").write_text(json.dumps({
        "finish": result.finish, "bound": result.bound, "costs": result.costs,
        "phases": [asdict(ph) for ph in result.phases]}, indent=1) + "\n")


def save_bound(bound: RelaxedBound, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bound.json").write_text(json.dumps(asdict(bound), indent=1) + "\n")


def load_program(in_dir: Path, show: Show) -> JudgingProgram:
    """The saved schedule, built from ``show``'s own events and pinned blocks."""
    saved = read_program(in_dir)
    events: dict[tuple, list[JudgingEvent]] = defaultdict(list)
    for e in show.events:
        events[_event_key(e)].append(e)
    pinned = {_pin_key(sb): sb for sb in show.pinned}
    judges = {j.name: j for j in show.judges}
    blocks = []
    for sb in saved.blocks:
        pin = pinned.get(_pin_key(sb))
        if pin is not None:
            blocks.append(pin)
            continue
        evs = tuple(events[_event_key(e)].pop(0) for e in sb.block.events)
        blocks.append(ScheduledBlock(Block(evs[0].judge, evs), sb.ring, sb.start))
    breaks = [Break(judges[br.judge.name], br.ring, br.start, br.minutes) for br in saved.breaks]
    plan = saved.groups
    groups = GroupsPlan(show.group_rings, plan.start, plan.order, dict(show.group_judges), show.bis_judge)
    return JudgingProgram(show, blocks, breaks, groups)


def load_result(in_dir: Path, show: Show) -> PlacementResult:
    meta = json.loads((in_dir / "solve.json").read_text())
    return PlacementResult(load_program(in_dir, show), meta["finish"], meta["bound"],
                           [PhaseStats(**ph) for ph in meta["phases"]], meta["costs"])


def parsed_days() -> list[Path]:
    return [d for d in sorted(PARSED.glob("*/*")) if (d / "schedule.csv").exists()]


def solved_dir(day: Path) -> Path:
    return SOLVED / day.parent.name / day.name


def read_day(day: Path, seed: int = 0) -> tuple[JudgingProgram, Show]:
    """The published program, with synthetic entries if any, and its instance."""
    human = read_program(day)
    synth = SYNTH / day.parent.name / day.name / f"seed-{seed}"
    if synth.exists():
        human.show.entries = read_entries(synth, human)
    return human, instance_from_program(human)


@dataclass
class SolvedDay:
    name: str
    human: JudgingProgram
    show: Show
    bound: RelaxedBound
    results: dict[str, PlacementResult]


def load_day(day: Path) -> SolvedDay:
    d = solved_dir(day)
    if not (d / "bound.json").exists():
        raise FileNotFoundError(f"{d} has no saved results: run python -m optdog.analysis.solve_all")
    human, show = read_day(day)
    bound = RelaxedBound(**json.loads((d / "bound.json").read_text()))
    return SolvedDay(day.name, human, show, bound, {s: load_result(d / s, show) for s in STEPS})
