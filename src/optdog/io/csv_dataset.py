"""The data/ directory tree as a source and sink of shows, entries and results.

    <root>/parsed/<source>/<slug>/*.csv                  published programs (see program_csv.py)
    <root>/synthetic/<source>/<slug>/seed-<n>/           dogs and exhibitors (see entries_csv.py)
    <root>/solved/<source>/<slug>/bound.json             the relaxation's lower bound
    <root>/solved/<source>/<slug>/<step>/*.csv           a solver result's schedule
    <root>/solved/<source>/<slug>/<step>/solve.json      its finish, proven bound, phases, costs

Times in the JSON files are minutes after midnight. ``CsvDataset`` implements every protocol in
``optdog.sources`` and names them as its bases; callers outside the I/O edge use it only through
them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields, replace
from pathlib import Path

from optdog.domain import Entry, JudgingEvent, JudgingProgram, TimeOfDay
from optdog.io.entries_csv import read_entries, write_entries
from optdog.io.program_csv import read_program, write_program
from optdog.solve.cpsat import PhaseStats, PlacementResult
from optdog.solve.relaxation import RelaxedBound
from optdog.sources import (
    EntrySink,
    EntrySource,
    ProgramSink,
    ProgramSource,
    ResultSink,
    ResultSource,
    ShowKey,
)

REPO_DATA = Path(__file__).resolve().parents[3] / "data"


def _minutes(t: TimeOfDay | None) -> float | None:
    return None if t is None else t.minutes


def _time(minutes: float | None) -> TimeOfDay | None:
    return None if minutes is None else TimeOfDay(minutes)


class CsvDataset(ProgramSource, ProgramSink, EntrySource, EntrySink, ResultSource, ResultSink):
    def __init__(self, root: Path = REPO_DATA) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """The directory holding parsed/, synthetic/ and solved/."""
        return self._root

    def __repr__(self) -> str:
        return f"CsvDataset({self._root})"

    # ----------------------------------------------------------------------- layout
    def program_dir(self, key: ShowKey) -> Path:
        return self.root / "parsed" / key.source / key.slug

    def entries_dir(self, key: ShowKey, seed: int = 0) -> Path:
        return self.root / "synthetic" / key.source / key.slug / f"seed-{seed}"

    def solved_dir(self, key: ShowKey) -> Path:
        return self.root / "solved" / key.source / key.slug

    def key_of(self, program_dir: Path) -> ShowKey:
        """The key of a program directory, <anything>/<source>/<slug>."""
        return ShowKey(program_dir.parent.name, program_dir.name)

    # ----------------------------------------------------------------------- programs
    def program_keys(self) -> list[ShowKey]:
        return sorted(self.key_of(d) for d in (self.root / "parsed").glob("*/*") if (d / "schedule.csv").exists())

    def program(self, key: ShowKey) -> JudgingProgram:
        return read_program(self.program_dir(key))

    def put_program(self, key: ShowKey, program: JudgingProgram, origin: str = "") -> None:
        write_program(program, self.program_dir(key), source=origin)

    # ----------------------------------------------------------------------- entries
    def entries(self, key: ShowKey, events: list[JudgingEvent], seed: int = 0) -> list[Entry] | None:
        d = self.entries_dir(key, seed)
        return read_entries(d, events) if d.exists() else None

    def put_entries(self, key: ShowKey, entries: list[Entry], seed: int = 0) -> None:
        write_entries(entries, self.entries_dir(key, seed))

    # ----------------------------------------------------------------------- results
    def bound(self, key: ShowKey) -> RelaxedBound | None:
        path = self.solved_dir(key) / "bound.json"
        if not path.exists():
            return None
        row = json.loads(path.read_text())
        return RelaxedBound(TimeOfDay(row["value"]), TimeOfDay(row["relaxation"]), TimeOfDay(row["combinatorial"]),
                            _time(row["relaxed_finish"]), row["status"], row["seconds"])

    def put_bound(self, key: ShowKey, bound: RelaxedBound) -> None:
        out = self.solved_dir(key)
        out.mkdir(parents=True, exist_ok=True)
        row = {f.name: getattr(bound, f.name) for f in fields(bound)}
        row = {k: _minutes(v) if isinstance(v, TimeOfDay) else v for k, v in row.items()}
        (out / "bound.json").write_text(json.dumps(row, indent=1) + "\n")

    def result(self, key: ShowKey, step: str) -> PlacementResult | None:
        d = self.solved_dir(key) / step
        if not (d / "solve.json").exists():
            return None
        meta = json.loads((d / "solve.json").read_text())
        return PlacementResult(read_program(d), TimeOfDay(meta["finish"]), TimeOfDay(meta["bound"]),
                               [PhaseStats(**ph) for ph in meta["phases"]], meta["costs"])

    def put_result(self, key: ShowKey, step: str, result: PlacementResult) -> None:
        # the dogs as the counts give them: their synthetic exhibitors stay with the entries
        out = self.solved_dir(key) / step
        program = result.program
        write_program(replace(program, show=replace(program.show, entries=[])), out, source="optdog")
        (out / "solve.json").write_text(json.dumps({
            "finish": _minutes(result.finish), "bound": _minutes(result.bound), "costs": result.costs,
            "phases": [asdict(ph) for ph in result.phases]}, indent=1) + "\n")
