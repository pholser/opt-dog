# opt-dog: the solution as it stands

As of commit `a35dac1` (2026-11-09). Rule numbers (H1, S9 and so on) and their sources are in
[`SPEC.md`](SPEC.md). Per-day results are in [`baseline.md`](baseline.md) and
[`figures.html`](figures.html).

## 1. What it does

opt-dog generates the judging program for a one-day AKC all-breed conformation show. Given the
judges and their breed assignments, the entries, the rings and the group and Best in Show (BIS)
judges, it decides:

- how each judge's breeds are ordered and split into judging blocks
- each block's ring and start time, on a 5-minute grid
- when each judge takes lunch
- the order of the seven groups, with BIS following the last group

The main goal is the earliest show finish that breaks no hard rule. It then reduces short blocks,
ring changes, equipment moves and judge idle time. Every rule traces to an AKC document in
`docs/akc/`, a decision by the project owner, or evidence from published programs.

Who judges what is an input. The program does not assign judges.

## 2. How it works

### Pipeline

| Stage | Code | Role |
|---|---|---|
| Ingest | `ingest/` | Parses published Bradshaw programs into `data/parsed/` |
| Domain model | `domain.py`, `instance.py`, `breeds.py` | Typed objects. CSV appears only at the I/O edges (`io/`) |
| Greedy schedule | `solve/greedy.py` | A fast valid schedule, used as the solver's starting point and as a baseline |
| Lower bounds | `solve/bounds.py`, `solve/relaxation.py` | The earliest finish any valid schedule could reach |
| Optimizer | `solve/cpsat.py`, `solve/packing.py` | CP-SAT model (details below) |
| Validator | `validate.py` | Checks any schedule against the rules, independently of the solver |
| Output | `render/`, `analysis/` | Text and HTML charts, comparison with published programs, reports |

### Optimizer steps

Each step gives the solver more freedom than the last. Step 3c is the current best and the one
that planning uses.

| Step | Function | What the solver decides |
|---|---|---|
| 1 Placement | `solve_placement` | Ring, time, lunches and group order, for the greedy's blocks |
| 3a Formation | `solve_formation` | Also where blocks break, keeping the greedy's running order |
| 3b Ordering | `solve_ordering` | Also the order of each judge's groups, and of ground, table and ramp runs within a group |
| 3c Packing | `solve_packing` | Also which breeds share a block, using a small bin-packing solve inside each run |

The 3c solve runs in phases, and each phase holds the results of the ones before it:

1. **Finish:** the earliest possible show finish.
2. **Trade:** give up to 10 minutes of finish per short block avoided (`short_block_finish_minutes`).
3. **Structure:** ring changes, short blocks, lunch placement, equipment switches and block count.
4. **Conflicts:** minutes that exhibitors signed up for the conflict-resolution program are needed
   in two rings at once. Exhibitors who did not opt in are ignored.
5. **Times:** least judge idle, then earliest block starts.

Each run starts from a complete valid solution found by the placement step. Each phase has a time
limit, 60 seconds by default.

### Rules in the solver

| Rule | Status |
|---|---|
| H1 block length, H3 one block per ring, H4 judge in one place, H5 published order | Enforced |
| H6 lunch, H7–H10 groups and BIS sequencing, H13 two groups done first, H14 groups after lunches | Enforced |
| H11 pinned events, H15 sweepstakes and multi-entry classes | Enforced |
| H16 ring big enough for the breed, H17 group ring size | Enforced. A ring of unknown size counts as XL |
| H12 closing time | **Validator only** (see section 5) |
| V1 judge's daily entry limit | Input check. The panel is given, so the solver cannot fix it |
| S1 exhibitor conflicts, opted-in exhibitors only | In the objective, after structure. Runs when the show has exhibitor entries |
| S2 ring changes, S5 idle, S9 short blocks, S10 lunch at a stopping point | In the objective |
| S3 equipment switches | In the objective, with one approximation (see section 5) |
| S4 groups kept together | Built into 3b and 3c: each judge's group is one run |
| S6, S7, S8 | **Not in the solver** (see section 5) |

### Ring sizes

Rings are S, M, L or XL, from the Show Manual's suggested dimensions. Each breed has a minimum:

- Toy breeds need S.
- Other table or ramp breeds need M.
- Ground breeds need L.
- A `min_ring` column in `data/reference/akc_breeds.csv` overrides the rule for a breed.
- Events with no breed need M, and group rings need XL.

Using the table/ramp list as a stand-in for size was a project decision, because the Manual does
not define "larger breeds".

## 3. Using it

Run these from the repository root with `.venv/bin/python`.

```sh
# Plan a show from inputs alone
python -m optdog.plan check   <problem dir>
python -m optdog.plan solve   <problem dir> --out <dir> --html chart.html --time-limit 60
python -m optdog.plan extract <parsed program dir> <problem dir>   # a published show's inputs

# Work with the published shows
python -m optdog.analysis.solve_all [--step packing]   # solve and save to data/solved/
python -m optdog.analysis.baseline                     # rebuild docs/baseline.md
python -m optdog.analysis.figures                      # rebuild docs/figures.html
python -m optdog.render <parsed program dir> --saved --compare
```

A problem directory holds:

| File | Contents |
|---|---|
| `show.csv` | Name, date, start, lunch window, BIS judge |
| `rings.csv` | Number, size, whether it is a group ring |
| `judges.csv` | Name, permit status, minutes per dog |
| `events.csv` | Judge, event, kind, breed, entry breakdown, optional pinned ring and start |
| `groups.csv` | Group and its judge |

`check` and `solve` exit with status 2 on input errors. `solve` exits with status 1 and writes
nothing if the result breaks a hard rule other than V1.

## 4. Results

These cover 11 published all-breed days from one superintendent (Bradshaw), 2022–2026.

| Day | Lower bound | Published | 3c | 3c status |
|---|---|---|---|---|
| Del Sur 2022-04-30 | 5:47 PM | 7:30 PM | 5:49 PM | Optimal |
| Del Sur 2022-05-01 | 5:00 PM | 6:25 PM | 5:00 PM | Optimal |
| Tucson 2025-11-14 | 6:29 PM | 8:14 PM | 6:39 PM | Optimal |
| Tucson 2025-11-15 | 6:47 PM | 8:37 PM | 6:52 PM | Optimal |
| Greater Sierra Vista 2025-11-16 | 7:19 PM | 8:44 PM | 7:19 PM | Optimal |
| Greater Sierra Vista 2025-11-17 | 6:10 PM | 7:15 PM | 6:10 PM | Optimal |
| Palm Springs 2026-01-03 | 8:15 PM | 9:20 PM | 8:24 PM | Optimal |
| Palm Springs 2026-01-04 | 7:44 PM | 8:44 PM | 7:54 PM | Optimal |
| Palm Springs 2026-01-05 | 9:59 PM | 10:04 PM | 10:04 PM | Optimal |
| Sir Francis Drake 2026-09-12 | 6:30 PM | 7:50 PM | 6:33 PM | Feasible, not proven |
| Sir Francis Drake 2026-09-13 | 7:23 PM | 8:18 PM | 7:27 PM | Feasible, not proven |

| Total over the 11 days | Published | 3b | 3c |
|---|---|---|---|
| Short blocks (under 15 dogs) | 64 | 50 | 22 |
| Equipment switches in a ring | 421 | 229 | 221 |
| Judge idle minutes | 1413 | 1205 | 1138 |
| Mean minutes above the lower bound | 74 | 7 | 4 |

- 11 of the 22 short blocks in 3c are a judge's whole assignment of under 15 dogs, so they cannot
  be made larger.
- 3c breaks no hard rule on any day, except V1 on Tucson 11/15. That is a panel limit the published
  program breaks too.
- 8 of the 11 published programs break at least one hard rule as this project defines them.

**How to read the clock times.** Group and BIS durations are deliberately conservative upper
bounds (SPEC §2.5), so modeled finishes run later than real shows close. Published programs are
timed with the same durations, so the comparison between columns is fair, but the absolute times
are not predictions.

**"Optimal" is narrower than it sounds.** For 3c it means proven best for the breed order that the
packing step fixed, at the trade price of 10 minutes per short block.

**Runtime.** A full batch of bound and all four steps took about 57 minutes for 11 days, or 2–9
minutes per day, before the conflict phase. That phase adds up to 60 seconds per step per day.

**Ring-size experiment.** Three days, with invented layouts, because no published program states
ring sizes. Each cell lists Del Sur 5/1, Palm Springs 1/4 and Sir Francis Drake 9/12.

| Layout | Finish change (min) | Judge ring changes | Judge idle (min) |
|---|---|---|---|
| All XL | n/a | 0 / 1 / 1 | 35 / 123 / 63 |
| Proportional to judging time | 0 / 0 / 0 | 1 / 2 / 2 | 32 / 274 / 453 |
| Half the L rings become M | 0 / 0 / +2.4 | 3 / 8 / 10 | 178 / 561 / 389 |

Limiting ring sizes barely moved the finish, but cost ring changes and idle time. The idle figures
are rough, because the last phase runs out of time on these runs, and they predate the change that
counts any wait beyond a lunch's length as idle, so they understate it.

**Tests.** All 278 pass, in about 7–11 minutes. The solver tests use short time limits, so they
should run with nothing else competing for CPU.

## 5. Unresolved and unaddressed

### Rules not yet modeled

| Item | Detail |
|---|---|
| Exhibitor input for planning | The conflict phase runs only when a show has entries, and the problem format has no exhibitor file, so `python -m optdog.plan` cannot use it yet. `baseline.md` reports conflicts only through step 1 (CP), not 3a–3c |
| Ring size by entries | Show Manual §6 also requires rings large enough for "the class with the largest number of entries". Only breed size is modeled. Entry thresholds are undecided |
| Similar-size dogs in a block | Show Manual §6. Not modeled. It would interact with 3c packing |
| Ring proximity | Show Manual §6. Needs venue layouts, and `adj` from SPEC §2.2 is not implemented |
| Closing time (H12) | The solver does not use it, and the problem format has no field for it. Only the validator checks it, and only if a closing time is known |
| Final segment over 50 dogs (S8) | Scored by the validator, not in the solver |
| Short-nosed breeds in the cool of the day (S6) | Not modeled |
| Permit judges at non-conflicting times (S7) | Not modeled |
| Equipment switch at a mid-day ring change (S3) | The solver does not count it. The validator counts it exactly |

### Decisions needed

| Item | Detail |
|---|---|
| Objective form | The original decision was a weighted sum, but the solver is lexicographic (SPEC §6). Several `ObjectiveWeights` values (finish, conflict, group switch, judge idle, final segment) affect only the validator's score |
| Soft-cost weights | Default weights have not been calibrated |
| Group ring | An input today, and `extract` copies it from the published program. SPEC §7 still asks whether the program should choose it |
| "Larger breeds" | The ground / table / ramp rule is a proxy. Individual breeds may need overrides |
| Group and BIS durations | Conservative by decision, so absolute finish times are not calibrated against real shows |
| Why humans start groups later | The model can start groups earlier than published programs. SPEC §8 lists possible causes that are not modeled |
| Exhibitor data model | Synthetic only. How realistic it needs to be is undecided |
| Conflicts against judge idle | The conflict phase runs before idle. On a 3-day trial it cut conflicts 56–99% at the same finish, but judge idle went 45 → 361 and 131 → 1243 minutes on two days and did not rise on the third. A 300-second idle phase brought those two to 225 and 717. Options not yet tried: a per-judge idle cap, or letting conflicts rise slightly during the idle phase |

### Solver limits

| Item | Detail |
|---|---|
| 3c is not globally optimal | Packing fixes the breed order before the main model runs. A full per-breed circuit was rejected as too large (2,000–4,800 arcs per day, each with a constraint per ring) |
| Two days unproven | Sir Francis Drake 9/12 and 9/13 end the finish and trade phases at the time limit |
| Last phases time out | The idle phase hit its limit on every day, and the conflict phase on every trial day, so neither is proven minimal |
| No ring symmetry breaking | The model has none. With mixed ring sizes it would need to respect size classes. It may also reduce the idle cost seen in the ring experiment |
| Lower bound ignores ring size | Still valid, but weaker when ring sizes bind |
| One unexplained solver failure | A test once ended with no solution after 3.5 seconds, under a 20-second limit. It did not recur, but the cause was never found |
| Saved results predate the conflict phase | `data/solved/` was produced before the starting-solution change and the conflict phase, so saved schedules do not reduce conflicts. Section 4's totals describe them |

### Evidence gaps

| Item | Detail |
|---|---|
| From-scratch CP-SAT | Run end to end on one day only (Del Sur 5/1). Input round-trips and the greedy path are tested on all 11 |
| Ring sizes | No real ring layout has been tested. The experiment covers three days with invented layouts |
| Single data source | Only Bradshaw programs are parsed. Baray, Rau and the Mid-Kentucky excerpt are in `data/raw/` but not ingested, and show-giving practice may differ by region or superintendent |
| Validator coverage | It does not independently check H5, H10, H11 or H15. Those hold by construction in the solver |
| No practitioner review | No superintendent or show chair has looked at a generated program |

### Out of scope for now

- Assigning or reassigning judges, including fixing a V1 overload
- Multi-day clusters: each day is planned alone
- Obedience and rally, beyond pinning a judge's commitments
- Exhibitors entered in both conformation and obedience or rally

### Documentation debt

- There is no README.
- The running entries in SPEC §9 give the figures at the time of each entry, so the earlier ones
  are historical. Current results are in section 4 and in `baseline.md`.
