# opt-dog

**Judging programs for AKC all-breed dog shows, generated as an optimization problem.**

Before every all-breed conformation show, the superintendent publishes a *judging program*: which
ring each judge works in, when each breed is judged, when judges break for lunch, and when the
seven groups and Best in Show begin. Building one means satisfying dozens of rules from the
AKC's rule book and Show Manual at once. opt-dog builds them automatically. It finds the
earliest finish that breaks no rule, then makes the day better for judges and exhibitors: fewer
short blocks, ring changes and equipment moves, less judge idle time, and fewer clashes for
exhibitors showing dogs in two rings.

## What it decides

Given the judges and their breed assignments, the entries, the rings, and the group and Best in
Show judges, opt-dog decides:

- how each judge's breeds are ordered and split into judging blocks of about an hour;
- each block's ring and start time, on a 5-minute grid;
- when each judge takes lunch;
- the order of the groups, with Best in Show following the last.

Who judges what is an input; opt-dog does not assign judges. Only regular breed judging is
scheduled: sweepstakes, juniors, obedience, rally and other events are out of scope.

Every rule traces to an AKC document (kept in [`docs/akc/`](docs/akc/)), a decision by the
project owner, or evidence from published programs. [SPEC.md](docs/SPEC.md) lists each one with
its source.

## Results so far

Tested on 14 published all-breed show days from two superintendents (11 from Jack Bradshaw in
California and Arizona, 2022–2026; 3 from Rau Dog Shows in New Jersey and Maryland, 2024),
comparing the published program with opt-dog's best solver step (3c), both timed the same way:

| Over the 14 days | Published | opt-dog |
|---|---|---|
| Mean minutes of finish above the proven lower bound | 98 | 4 |
| Days within 10 minutes of the lower bound | 1 | 14 |
| Short blocks (under 15 dogs) | 59 | 15 |
| Judge ring changes outside lunch | 32 | 0 |
| Exhibitor-conflict minutes (synthetic exhibitors) | 17,466 | 7,674 |
| Judge idle minutes | 1,657 | 1,724 |

The schedules break no hard rule on any day, including the posted closing time where a program
gives one; 8 of the 14 published programs break at least one as this project defines them. On
the large Bradshaw days, judge idle time is the price paid for fewer exhibitor conflicts; on the
smaller Rau days both go down.

Two cautions. Group and Best in Show durations are deliberately conservative, so the absolute
clock times run later than real shows close; they are for comparison, not prediction. And
exhibitors are synthetic, because published programs do not say who shows which dog. Details,
per-day figures and open questions are in [SOLUTION.md](docs/SOLUTION.md) and
[baseline.md](docs/baseline.md); the charts are in [figures.html](docs/figures.html).

## Quick start

Requires Python 3.12 or later.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Plan a real show from its inputs: take a published show's inputs, check them, and solve.

```sh
SHOW=2026-09-12-sir-francis-drake-kennel-club
.venv/bin/python -m optdog.plan extract data/parsed/bradshaw/$SHOW /tmp/sfd \
    --entries data/synthetic/bradshaw/$SHOW/seed-0
.venv/bin/python -m optdog.plan check /tmp/sfd
.venv/bin/python -m optdog.plan solve /tmp/sfd --out /tmp/sfd-program --html /tmp/sfd.html --time-limit 10
```

`solve` prints the finish and the exhibitor conflicts, writes the program as CSV files, and
writes an HTML chart of the day. To compare a published program with the saved solver result:

```sh
.venv/bin/python -m optdog.render data/parsed/bradshaw/$SHOW --saved --compare
```

[RUNNING.md](docs/RUNNING.md) covers every program: the planner, the viewer, and the research
pipeline that parses published programs, measures them, re-solves them and reports.

## How it works

1. **Ingest.** Published PDF programs are parsed into a typed domain model (`ingest/`,
   `domain.py`). The parser checks itself against each program's printed totals and breed index.
2. **Greedy schedule.** A fast heuristic builds a valid schedule, used as a starting point and
   a baseline (`solve/greedy.py`).
3. **Lower bounds.** Combinatorial bounds and a CP-SAT relaxation give the earliest finish any
   valid schedule could reach (`solve/bounds.py`, `solve/relaxation.py`).
4. **Optimization.** A Google OR-Tools CP-SAT model decides blocks, rings, times, lunches and
   group order. It minimizes in phases: finish first, then short blocks, then structure (ring
   changes, equipment moves), then judge idle, then exhibitor conflicts within an idle budget
   (`solve/cpsat.py`, `solve/packing.py`).
5. **Validation.** An independent checker scores any schedule, human or generated, against the
   same rules (`validate.py`).

[FORMULATION.md](docs/FORMULATION.md) states the model in English and mathematics, with the
reasons behind each modeling choice.

## Repository layout

```
src/optdog/
  domain.py, breeds.py, instance.py   the domain model: shows, judges, events, blocks, times
  params.py                           rule thresholds, tolerances and weights
  sources.py, io/                     data sources, and the CSV format at the I/O edge
  ingest/                             parsing published PDF programs
  synth/                              synthetic exhibitors
  solve/                              greedy, bounds, relaxation, CP-SAT model
  validate.py, conflicts.py           rule checking and exhibitor conflicts
  plan.py                             the planning command
  render/                             text, comparison and HTML output
  analysis/                           calibration, batch solving, reports and figures
data/
  raw/          published PDFs (public; parsed, never executed) and AKC breed pages
  reference/    the AKC breed catalog
  parsed/       published programs as CSV
  synthetic/    generated exhibitors
  solved/       saved solver results
docs/           specification, formulation, results, run guide, AKC source documents
tests/          pytest suite
```

## Documentation

| Document | What it is |
|---|---|
| [SPEC.md](docs/SPEC.md) | The requirements: every hard rule and soft cost, with its source |
| [FORMULATION.md](docs/FORMULATION.md) | The optimization model, mathematically, with rationale |
| [SOLUTION.md](docs/SOLUTION.md) | What is built, results, and what is unresolved |
| [RUNNING.md](docs/RUNNING.md) | How to run each program, and why it exists |
| [calibration.md](docs/calibration.md) | How human superintendents actually schedule, measured |
| [baseline.md](docs/baseline.md) | Per-day comparison of published, greedy and solver schedules |
| [figures.html](docs/figures.html) | The same comparison as charts (open locally) |

## Development

```sh
.venv/bin/python -m pytest     # about six minutes; the solver tests dominate
.venv/bin/mypy                 # type-check src/optdog
```

## Status

A research prototype. The model is tested against published programs from two superintendents
(Jack Bradshaw and Rau Dog Shows), the only ones with parsers so far. The planner works end to end on
real inputs, but planning a real show would need its actual group and Best in Show durations
and ring layout, and real exhibitor sign-ups.
