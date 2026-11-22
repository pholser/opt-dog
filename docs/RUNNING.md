# Running opt-dog

What each program in this repository is for, and how to run it. The problem, the rules and the
model are described in [SPEC.md](SPEC.md), [FORMULATION.md](FORMULATION.md) and
[SOLUTION.md](SOLUTION.md).

## Setup

Python 3.12 or later. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'     # ortools, pdfplumber; pytest and mypy for development
```

Every command below runs from the repository root as `.venv/bin/python -m <module>`; the
examples write `python` for short.

## At a glance

| Program | Why it exists | Reads | Writes |
|---|---|---|---|
| [`optdog.plan`](#plan-a-show-optdogplan) | Plan a real show from its inputs: the product | a problem directory | a program directory, an HTML chart |
| [`optdog.render`](#look-at-one-show-optdogrender) | Look at one show's schedules, and compare them | `data/parsed`, `data/synthetic`, `data/solved` | text or HTML |
| [`optdog.ingest.build_dataset`](#1-parse-the-published-programs-optdogingestbuild_dataset) | Turn published PDF programs into data | `data/raw/<superintendent>` | `data/parsed` |
| [`optdog.ingest.pdftext`](#inspect-a-pdf-optdogingestpdftext) | See the text the parser sees | one PDF | stdout |
| [`optdog.synth.exhibitors`](#2-make-up-exhibitors-optdogsynthexhibitors) | Give the published shows exhibitors, for conflict scoring | `data/parsed` | `data/synthetic` |
| [`optdog.analysis.calibrate`](#3-measure-human-practice-optdoganalysiscalibrate) | Measure how superintendents really schedule | `data/parsed` | `docs/calibration.md` |
| [`optdog.analysis.solve_all`](#4-solve-every-show-optdoganalysissolve_all) | Solve every published show once and keep the results | `data/parsed`, `data/synthetic` | `data/solved` |
| [`optdog.analysis.baseline`](#5-compare-humans-and-solvers-optdoganalysisbaseline) | Compare human, greedy and solver schedules | everything under `data/` | `docs/baseline.md` |
| [`optdog.analysis.figures`](#6-chart-the-results-optdoganalysisfigures) | Chart the same comparison | everything under `data/` | `docs/figures.html` |

There are two ways in. **Planning** (`optdog.plan`) is what a superintendent would use: inputs in,
judging program out. **Research** (the pipeline below) checks the model against the published
programs of real shows: it parses them, measures them, re-solves them and compares.

## Plan a show: `optdog.plan`

The end use: given a show's panel, rings and entries, produce a judging program that keeps every
hard rule and finishes as early as it can. The inputs are a *problem directory* of CSV files,
described in [SOLUTION.md §3](SOLUTION.md#3-using-it) and `src/optdog/io/problem_csv.py`.

```sh
python -m optdog.plan check   <problem dir>
python -m optdog.plan solve   <problem dir> [--out <dir>] [--html chart.html] [--time-limit 60]
python -m optdog.plan extract <parsed program dir> <problem dir> [--entries <dir>]
```

- **`check`** reads the inputs and reports anything that would stop a valid program before any
  solving: errors such as no group ring, a group with entries but no judge, a breed that needs a
  larger ring than the show has, pinned blocks that overlap, or a closing time earlier than any
  schedule could finish; and warnings such as a judge over
  the daily entry limit or an event listed twice. It prints `no errors`, or the problems, and
  exits with status 2 on an error or on inputs it cannot read.
- **`solve`** runs the full solver (step 3c, packing; see SOLUTION.md), validates the result and
  writes it. `--out` writes the program as a directory of CSV files in the same format as a
  parsed one, so `optdog.render` and the validator can read it; `--html` writes a chart; with
  neither, the program is printed as text. `--time-limit` is CP-SAT's limit per phase in
  seconds (the idle and conflict phases get three times as long), so lower it for a quick
  answer and raise it for a better one. It reports the finish and whether it was proven optimal,
  and, when some exhibitors are signed up for conflict resolution, their conflict minutes. If the
  schedule breaks a hard rule it is not written and the exit status is 1, as it is when no
  schedule can finish by the show's closing time.
- **`extract`** turns a published show into a problem, so the planner can be tried on real
  inputs. Without `--entries` the dogs are generated from the printed counts, with no
  exhibitors, and the show is planned without regard to conflicts. `--entries` takes dogs with
  exhibitors from a directory such as `data/synthetic/bradshaw/<show>/seed-0`.

A full round trip on a real show:

```sh
python -m optdog.plan extract data/parsed/bradshaw/2026-09-12-sir-francis-drake-kennel-club /tmp/sfd \
    --entries data/synthetic/bradshaw/2026-09-12-sir-francis-drake-kennel-club/seed-0
python -m optdog.plan check /tmp/sfd
python -m optdog.plan solve /tmp/sfd --out /tmp/sfd-program --html /tmp/sfd.html --time-limit 10
```

## Look at one show: `optdog.render`

For reading schedules, and for seeing what the solver changed relative to the published
program. It renders a parsed show's published program, and optionally a greedy schedule, a
fresh solver run or a saved solver result for the same show.

```sh
python -m optdog.render <parsed program dir> [options]
```

| Option | Effect |
|---|---|
| `--greedy`, `--greedy-only` | add (or show only) the greedy baseline |
| `--solve [step]` | solve now and add the result; `step` is `placement`, `formation`, `ordering` or `packing` (default) |
| `--cp` | the same as `--solve placement` |
| `--saved [step]` | add a saved result from `data/solved` instead of solving (default `packing`) |
| `--compare [judge\|ring]` | print the first and last program side by side, aligned by judge (default) or ring |
| `--html <file>` | write an HTML chart of every program |
| `--text` | print the text programs (the default without `--html`) |
| `--out <file>` | write the text to a file instead of stdout |
| `--width`, `--time-limit`, `--seed` | column width for `--compare`; CP-SAT seconds per phase; synthetic-exhibitor seed |

Exhibitor conflicts are scored when the show has synthetic entries for `--seed` (default 0).

```sh
D=data/parsed/bradshaw/2026-09-12-sir-francis-drake-kennel-club
python -m optdog.render $D                          # the published program as text
python -m optdog.render $D --saved --compare        # published vs the saved 3c schedule
python -m optdog.render $D --greedy --html out.html # a chart of published and greedy
```

## The research pipeline

Run in this order; each step reads what the ones before it wrote. Everything it writes is
checked in, so you only need to rerun from the step whose inputs changed.

```sh
python -m optdog.ingest.build_dataset   # 1. data/raw → data/parsed
python -m optdog.synth.exhibitors       # 2. data/parsed → data/synthetic
python -m optdog.analysis.calibrate     # 3. → docs/calibration.md
python -m optdog.analysis.solve_all     # 4. → data/solved (long-running)
python -m optdog.analysis.baseline      # 5. → docs/baseline.md
python -m optdog.analysis.figures       # 6. → docs/figures.html
```

| If you changed… | Rerun from |
|---|---|
| the parser, or the raw PDFs or manifest | 1 |
| the exhibitor model | 2 |
| the validator or parameters, and only want the human measurements | 3 |
| the solver, the model or its parameters | 4 |
| only a report's wording or layout | 5 or 6 |

Steps 1–3, 5 and 6 take seconds. Step 4 solves every day with every solver step, each phase up
to its time limit: the last full run took about 3.5 hours for the 11 Bradshaw days, and each
Rau or MB-F day took 15–20 minutes (SOLUTION.md §4).

### 1. Parse the published programs: `optdog.ingest.build_dataset`

Real programs are the evidence: the constraints are checked against them and the solver is
compared with them. Each superintendent lays out programs differently and has its own parser:
Jack Bradshaw (`ingest/bradshaw.py`), Rau Dog Shows (`ingest/rau.py`) and MB-F (`ingest/mbf.py`). This step parses each
PDF listed in `data/raw/<superintendent>/manifest.csv` (rows with `include` = 1; a row left out
says why in its notes) and writes one directory per show day to
`data/parsed/<superintendent>/<date>-<club>/`: the program as CSV, plus `report.md`, which records
the parser's checks against the program's own figures (ring or judge totals, the breed index,
the number of breeds) and what it left out (sweepstakes, juniors, Beginner Puppy and other events
outside regular breed judging). Checks that fail because the program contradicts itself are
listed in the parser's tests. It prints a line per day with how many checks passed.

```sh
python -m optdog.ingest.build_dataset          # every superintendent
python -m optdog.ingest.build_dataset rau      # only these
```

The Onofrio and Baray programs and the Mid-Kentucky excerpt in `data/raw/` (see `data/raw/SOURCES.md`) have
no parser yet. The PDFs are untrusted input: they are parsed, never executed.

### Inspect a PDF: `optdog.ingest.pdftext`

A debugging aid for the parser: prints the text of a PDF column by column, split where the
parser splits two-column pages, under `=== PAGE <n><L|R> ===` headings.

```sh
python -m optdog.ingest.pdftext data/raw/bradshaw/50.pdf | less
```

### 2. Make up exhibitors: `optdog.synth.exhibitors`

Published programs give entry counts but not who shows which dog, and exhibitor conflicts (S1)
need exhibitors. This step generates them for every parsed show, from a seeded model of
professional handler teams and owner-handlers, some signed up for conflict resolution (the model
and its parameters are in the module's docstring and `ExhibitorModel`). It writes
`data/synthetic/bradshaw/<show>/seed-0/entries.csv` and `exhibitors.csv` and prints a summary per
day. The data is reproducible from the seed; the parameters are guesses, not measurements.

### 3. Measure human practice: `optdog.analysis.calibrate`

Grounds the model's parameters in what superintendents actually do: the judging rate their
published times imply, block sizes, lunch times and lengths, judge loads, when groups start,
and which hard rules the human programs break when timed at AKC's 25 dogs an hour. Writes
`docs/calibration.md` and prints it.

### 4. Solve every show: `optdog.analysis.solve_all`

Solving takes minutes per show, so this runs once and saves the results for the reports and for
`render --saved`. For each parsed day it computes the lower bound on the finish, then runs each
solver step (placement, formation, ordering, packing), validates each result, and saves the
valid ones to `data/solved/<superintendent>/<show>/`. A result that breaks a hard rule is not saved
(a judge over the entry limit, V1, is an input problem and is allowed). It prints one line per
day and exits with status 1 if any step failed.

```sh
python -m optdog.analysis.solve_all                    # every day, every step
python -m optdog.analysis.solve_all --only 2026-01     # days whose name contains this
python -m optdog.analysis.solve_all --step packing     # one step; keep the saved bound and other steps
python -m optdog.analysis.solve_all --time-limit 120   # CP-SAT seconds per phase (default 60)
```

Results depend on the time limit and the machine: CP-SAT stops at the limit with the best
schedule found, so a rerun can differ where a phase was not proven optimal.

### 5. Compare humans and solvers: `optdog.analysis.baseline`

The main scorecard: per day, the published program, the greedy baseline and each saved solver
step side by side, with the lower bound, each schedule's group start, finish and gap to the
bound, its hard-rule violations, its exhibitor-conflict minutes (from the synthetic entries) and
its equipment switches. Writes `docs/baseline.md`. Needs step 4's
results.

### 6. Chart the results: `optdog.analysis.figures`

The same comparison as figures: finish against the lower bound, group start, soft costs, block
sizes, and which rule binds each day. One self-contained HTML page with inline SVG and a data
table under each figure, so it opens offline. Writes `docs/figures.html`. Needs step 4's results.

## Checking the code

```sh
.venv/bin/python -m pytest      # the test suite (about six minutes; the solver tests dominate)
.venv/bin/mypy                  # type-check src/optdog
```

## Where the data lives

All data access goes through `optdog.sources` (the interfaces) and
`optdog.io.csv_dataset.CsvDataset` (the directory layout below); see those modules to read the
data from code or to add a source in another format.

```
data/raw/<superintendent>/          published PDFs and manifest.csv           (input)
data/reference/akc_breeds.csv       breed catalog: group, examination, ring-size override (input)
data/parsed/<source>/<show>/        a published program as CSV, and report.md  (step 1)
data/synthetic/<source>/<show>/seed-<n>/  dogs with exhibitors                 (step 2)
data/solved/<source>/<show>/        bound.json, and <step>/ with the schedule and solve.json (step 4)
```
