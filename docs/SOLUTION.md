# opt-dog: the solution as it stands

As of 2026-11-14. Rule numbers (H1, S9 and so on) and their sources are in
[`SPEC.md`](SPEC.md), and the optimization model in [`FORMULATION.md`](FORMULATION.md). Per-day
results are in [`baseline.md`](baseline.md) and [`figures.html`](figures.html).

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

Who judges what is an input. The program does not assign judges. Only regular breed events
exist for it: sweepstakes, Junior Showmanship, Beginner Puppy, FSS, specialty, obedience and
rally events are dropped when published programs are parsed, and the domain model has no way to
represent them.

## 2. How it works

### Pipeline

| Stage | Code | Role |
|---|---|---|
| Ingest | `ingest/` | Parses published Bradshaw and Rau programs into `data/parsed/`, one parser per superintendent |
| Domain model | `domain.py`, `instance.py`, `breeds.py` | Typed objects. CSV appears only at the I/O edges (`io/`). Times of day are `TimeOfDay` and lengths of time `Duration`; neither mixes with plain numbers, and they become integers only inside the CP-SAT models and the files |
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
2. **Trade:** give up to 10 minutes of finish per short block avoided (`short_block_finish`).
3. **Structure:** ring changes, short blocks, lunch placement, equipment switches and block count.
4. **Idle:** least judge idle. Idle may then grow to `conflict_idle_budget` times this minimum
   (default 2) to make room for the next phase. This phase gets three times the usual limit
   (`idle_time_limit`, 180 seconds by default), because a weak minimum widens the budget.
5. **Conflicts:** minutes that exhibitors signed up for the conflict-resolution program are needed
   in two rings at once, within that idle budget. Exhibitors who did not opt in are ignored. This
   phase also gets three times the usual limit (`conflict_time_limit`).
6. **Times:** least judge idle again, then earliest block starts.

Phases 4 and 5 run only when some opted-in exhibitor has two events that can clash.

Each run starts from a complete valid solution found by the placement step. Each phase has a time
limit, 60 seconds by default.

### Rules in the solver

| Rule | Status |
|---|---|
| H1 block length, H3 one block per ring, H4 judge in one place, H5 published order | Enforced |
| H6 lunch, H7–H10 groups and BIS sequencing, H13 two groups done first, H14 groups after lunches | Enforced |
| H11 pinned events, H15 multi-entry classes | Enforced |
| H16 ring big enough for the breed, H17 group ring size | Enforced. A ring of unknown size counts as XL |
| H12 closing time | Enforced when the show has one |
| V1 judge's daily entry limit | Input check. The panel is given, so the solver cannot fix it |
| S1 exhibitor conflicts, opted-in exhibitors only | In the objective, after structure, within a judge-idle budget. Runs when the show has exhibitor entries |
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

How to run every program, and what each is for, is in [RUNNING.md](RUNNING.md). In short:

```sh
python -m optdog.plan check <problem dir>
python -m optdog.plan solve <problem dir> --out <dir> --html chart.html
```

A problem directory holds:

| File | Contents |
|---|---|
| `show.csv` | Name, date, start, lunch window, closing time (optional), BIS judge |
| `rings.csv` | Number, size, whether it is a group ring |
| `judges.csv` | Name, permit status, minutes per dog |
| `events.csv` | Judge, event, breed, optional pinned ring and start |
| `groups.csv` | Group and its judge |
| `entries.csv` | One row per dog: dog, sex (D/B), status (class, nonregular or champion), exhibitor (may be blank), judge, event |
| `exhibitors.csv` (only if a dog names an exhibitor) | Exhibitor id, professional, signed up for conflict resolution, team |

Every breed's entry total and breakdown are counted from `entries.csv`, and every event needs
at least one dog. A parsed program carries an `entries.csv` too, generated from its printed
counts with no exhibitors; `extract` copies it. Without exhibitors, or with no one signed up, the
show is planned with no regard to exhibitor conflicts: the conflict phases are skipped and the
solver goes from structure to times. One exhibitor is one person who takes dogs into the ring:
each member of a professional handler's team is an exhibitor of their own and signs up on
their own, and `team` is only a label. With registered exhibitors present, `solve` runs the
conflict phases and reports conflict minutes. `check` warns when a dog is entered twice in one
event and when nobody is signed up. `extract --entries` takes the dogs and their exhibitors from
another directory instead, such as `data/synthetic/bradshaw/<show>/seed-0`, whose counts must
match the program's.

`check` and `solve` exit with status 2 on input errors. `solve` exits with status 1 and writes
nothing if the result breaks a hard rule other than V1.

## 4. Results

These cover 14 published all-breed days from two superintendents: 11 from Jack Bradshaw (California
and Arizona, 2022–2026) and 3 from Rau Dog Shows (New Jersey and Maryland, 2024).

| Day | Lower bound | Published | 3c | 3c status |
|---|---|---|---|---|
| Del Sur 2022-04-30 | 5:28 PM | 7:30 PM | 5:35 PM | Optimal |
| Del Sur 2022-05-01 | 5:00 PM | 6:25 PM | 5:00 PM | Optimal |
| Tucson 2025-11-14 | 6:04 PM | 8:14 PM | 6:04 PM | Optimal |
| Tucson 2025-11-15 | 6:47 PM | 8:37 PM | 6:52 PM | Optimal |
| Greater Sierra Vista 2025-11-16 | 5:59 PM | 8:44 PM | 5:59 PM | Optimal |
| Greater Sierra Vista 2025-11-17 | 6:10 PM | 7:15 PM | 6:10 PM | Optimal |
| Palm Springs 2026-01-03 | 8:15 PM | 9:20 PM | 8:24 PM | Optimal |
| Palm Springs 2026-01-04 | 7:44 PM | 8:44 PM | 7:54 PM | Optimal |
| Palm Springs 2026-01-05 | 9:59 PM | 10:04 PM | 10:04 PM | Feasible, not proven |
| Sir Francis Drake 2026-09-12 | 6:30 PM | 7:50 PM | 6:30 PM | Optimal |
| Sir Francis Drake 2026-09-13 | 7:23 PM | 8:18 PM | 7:27 PM | Optimal |
| Anne Arundel 2024-05-16 (Rau) | 5:03 PM | 7:43 PM | 5:08 PM | Optimal |
| Anne Arundel 2024-05-17 (Rau) | 5:07 PM | 7:52 PM | 5:12 PM | Optimal |
| Twin Brooks 2024-07-15 (Rau) | 3:51 PM | 5:36 PM | 3:51 PM | Optimal |

| Total over the 14 days (Bradshaw + Rau) | Published | 3b | 3c |
|---|---|---|---|
| Short blocks (under 15 dogs) | 59 (55 + 4) | 45 (44 + 1) | 15 (14 + 1) |
| Equipment switches in a ring | 436 (358 + 78) | 206 (166 + 40) | 203 (163 + 40) |
| Judge ring changes (outside lunch) | 32 (21 + 11) | 0 | 0 |
| Exhibitor conflict minutes (opted-in, synthetic seed 0) | 17,466 (16,937 + 529) | 7,491 (7,401 + 90) | 7,674 (7,541 + 133) |
| Judge idle minutes | 1,657 (1,258 + 399) | 2,146 (1,911 + 235) | 1,724 (1,549 + 175) |
| Mean minutes above the lower bound | 98 (86, 143) | 6 (7, 2) | 4 (4, 3) |

- Conflicts in 3c are lower than published on every day: 55% lower in total, 39–98% lower on
  the eight smaller days, and 47–61% lower on the three Palm Springs days, where most
  conflict minutes are.
- Judge idle is the price on the Bradshaw days: 3c has 1,549 idle minutes, more than the
  published programs' 1,258. The budget allows up to twice the least idle the idle phase finds.
  On the Rau days idle falls instead (399 → 175), with conflicts down 75% (529 → 133).
- **The Rau days start groups two hours earlier than published.** 3c starts groups at
  11:05–11:15 AM; the programs start them at 1:00–1:45 PM. No Rau judge has more than 120 entries
  (under five hours), so no one needs a lunch and H14 does not hold groups back. This sharpens the
  open question of why humans start groups later (section 5).
- **Closing time.** The Rau programs print show hours, so those days carry a closing time (H12):
  7:00 PM at Anne Arundel, 6:30 PM at Twin Brooks. 3c meets it with 1½–2½ hours to spare. Both
  published Anne Arundel programs finish after it (7:43 and 7:52 PM) when timed with the model's
  conservative group and BIS durations, which is why the validator flags them.
- No judge changes rings outside lunch in 3b or 3c. The 12 and 16 changes of earlier runs came
  from pinned Beginner Puppy, FSS and specialty blocks, which are no longer scheduled.
- Phase limits mattered. With 60-second idle and conflict phases, 3c had 10,361 conflict
  minutes and 2,183 idle. A 180-second idle phase tightened the budget (Palm Springs 1/5:
  cap 511 → about 338, idle used 511 → 239), giving 10,194 and 1,703. A 180-second conflict
  phase then gave 7,408 and 1,913. Those runs still scheduled the non-regular events; with
  regular breed events only, 3c has 7,541 and 1,549.
- Per-day figures are one sample. The conflict phase never proves a minimum, and between runs
  a day's conflicts can move by a factor of three (Greater Sierra Vista 11/17: 102, 301 and 81)
  and its idle by a factor of two (Tucson 11/14: 157 and 308).

- 3 of the 14 short blocks in 3c on the Bradshaw days are a judge's whole assignment of under 15
  dogs, so they cannot be made larger.
- 3c breaks no hard rule on any day. No judge is over the V1 limit once juniors and sweepstakes
  are left out (the heaviest has 175 entries).
- 8 of the 14 published programs (6 Bradshaw, 2 Rau) break at least one hard rule as this project
  defines them.

**How to read the clock times.** Group and BIS durations are deliberately conservative upper
bounds (SPEC §2.5), so modeled finishes run later than real shows close. Published programs are
timed with the same durations, so the comparison between columns is fair, but the absolute times
are not predictions.

**"Optimal" is narrower than it sounds.** For 3c it means proven best for the breed order that the
packing step fixed, at the trade price of 10 minutes per short block.

**Runtime.** A full batch of bound and all four steps took about 57 minutes for 11 days, or 2–9
minutes per day, before the conflict phases. With them, at 180 seconds each, the batch took
about 3.5 hours, or 13–25 minutes per day. The three Rau days took 15–20 minutes each.

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

**Tests.** All 296 pass, in about 6 minutes. The solver tests use short time limits, so they
should run with nothing else competing for CPU.

**Type checking.** `mypy` (a dev extra) checks `src/optdog` clean. `solve/cpsat.py` is exempt
(`pyproject.toml`): its model keeps decided cuts and arcs as Python constants in the slots CP-SAT
literals use, which OR-Tools' type stubs cannot express.

## 5. Unresolved and unaddressed

### Rules not yet modeled

| Item | Detail |
|---|---|
| Ring size by entries | Show Manual §6 also requires rings large enough for "the class with the largest number of entries". Only breed size is modeled. Entry thresholds are undecided |
| Similar-size dogs in a block | Show Manual §6. Not modeled. It would interact with 3c packing |
| Ring proximity | Show Manual §6. Needs venue layouts, and `adj` from SPEC §2.2 is not implemented |
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
| Exhibitor data model | Synthetic only. The problem format accepts real entries, but no real entry data has been tried, and nothing reads a superintendent's entry export. How realistic synthetic data needs to be is undecided |
| Conflicts against judge idle | Settled for now with an idle budget (default 2 × least idle). Conflicts first had cut conflicts more but let idle grow to 361 and 1,243 minutes on two trial days. With the budget, Del Sur 5/1 went from 138 to 9 conflict minutes (idle 45 → 88), and Palm Springs 1/4 from 3,524 to 2,695 (idle 131 → 238). On big days the budget limits how many conflicts are removed. The budget is a multiple, so a day whose least idle is 0 cannot spend idle on conflicts. A per-judge cap is not tried |

### Solver limits

| Item | Detail |
|---|---|
| 3c is not globally optimal | Packing fixes the breed order before the main model runs. A full per-breed circuit was rejected as too large (2,000–4,800 arcs per day, each with a constraint per ring) |
| Three days unproven | Sir Francis Drake 9/12 and 9/13, and in this run Palm Springs 1/5, end the finish and trade phases at the time limit. Palm Springs 1/5 was proven in an earlier run with the same finish, so its result depends on the run |
| Last phases time out | In 3c the conflict phase hits its limit on every day, the idle phase on 10 of 11, and the structure phase on two (Palm Springs 1/5 and Sir Francis Drake 9/13), so none of them is proven minimal |
| No ring symmetry breaking | The model has none. With mixed ring sizes it would need to respect size classes. It may also reduce the idle cost seen in the ring experiment |
| Lower bound ignores ring size | Still valid, but weaker when ring sizes bind |
| One unexplained solver failure | A test once ended with no solution after 3.5 seconds, under a 20-second limit. It did not recur, but the cause was never found |
| Idle budget rests on an unproven minimum | The idle phase still times out, so the budget can be wider than 2 × the true least idle. At 180 seconds, the phase was within 1 minute of what 300 seconds found on Palm Springs 1/5. A cap in minutes would remove the dependence |
| Conflict phase is noisy | Its lower bound stays at 0, so it is never proven. Per-day conflict minutes vary by up to a factor of three between runs, even at 180 seconds |

### Evidence gaps

| Item | Detail |
|---|---|
| From-scratch CP-SAT | Run end to end on one day only (Del Sur 5/1). Input round-trips and the greedy path are tested on all 11 |
| Ring sizes | No real ring layout has been tested. The experiment covers three days with invented layouts |
| Two data sources | Bradshaw (11 days, California and Arizona) and Rau (3 days, New Jersey and Maryland) are parsed. Baray and the Mid-Kentucky excerpt are in `data/raw/` but not ingested, and the larger superintendents (MB-F, Onofrio) are not represented |
| Validator coverage | It does not independently check H5, H10, H11 or H15. Those hold by construction in the solver |
| No practitioner review | No superintendent or show chair has looked at a generated program |

### Out of scope for now

- Assigning or reassigning judges, including fixing a V1 overload
- Multi-day clusters: each day is planned alone
- Every event other than regular breed judging: sweepstakes, Junior Showmanship, Beginner Puppy,
  FSS open shows, concurrent specialties, obedience and rally

### Documentation debt

- The running entries in SPEC §9 give the figures at the time of each entry, so the earlier ones
  are historical. Current results are in section 4 and in `baseline.md`.
