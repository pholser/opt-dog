# AKC All-Breed Show Scheduling

Automated scheduling system for AKC all-breed conformation dog shows. Reads a structured
show input workbook, applies AKC rules and constraints, and produces an optimized judging
program whose primary goal is to minimize the Best-in-Show start time.

---

## Table of Contents

1. [Problem Description](#1-problem-description)
2. [AKC Rules and Constraints](#2-akc-rules-and-constraints)
3. [Solution Approach](#3-solution-approach)
4. [Preprocessing](#4-preprocessing)
5. [Mathematical Model](#5-mathematical-model)
6. [Objective](#6-objective)
7. [Post-hoc Arena Assignment](#7-post-hoc-arena-assignment)
8. [Output](#8-output)
9. [Files](#9-files)
10. [Running the Solver](#10-running-the-solver)
11. [Known Limitations and Deferred Features](#11-known-limitations-and-deferred-features)

---

## 1. Problem Description

An AKC all-breed conformation dog show is a single-day event in which purebred dogs compete
for the title of Best in Show (BIS). Competition proceeds in three stages:

**Breed judging.** Within each breed, dogs compete by sex and class (class dogs → class
bitches → specials, i.e., champions). The breed judge awards Winners Dog, Winners Bitch,
and Best of Breed (BOB).

**Group judging.** The seven BOB winners from each AKC group (Sporting, Hound, Working,
Terrier, Toy, Non-Sporting, Herding) compete before a group judge. Group judging cannot
begin until all breed judging within that group is complete.

**Best in Show.** The seven group winners compete before the BIS judge. BIS cannot begin
until all group judging is complete.

A typical large all-breed show involves:
- 175–200 AKC breeds and varieties
- 1,500–4,000 individual dog entries
- 8–15 judging rings operating simultaneously
- 12–30 judges, each assigned to one or more breeds plus optionally a group
- 7–9 hours of judging

The show committee fixes which judge judges which breeds before the optimizer runs. The
optimizer decides:

1. Which physical ring each judge's *segment* (block of breeds) occupies, and
2. When each segment starts.

The official output — the **judging program** — specifies, for every ring, the sequence of
breeds and their approximate start times. Today this program is produced manually by
experienced show superintendents. Automation produces optimal or near-optimal schedules in
minutes and responds instantly to entry changes.

---

## 2. AKC Rules and Constraints

### 2.1 Judging Rates

| Judge type | Rate | Source |
|---|---|---|
| Standard | 2.4 min/dog (25 dogs/hr) | AKC Show Manual §6 |
| Permit    | 3.0 min/dog (20 dogs/hr) | AKC Show Manual §6 |

Per-judge override supported via the `Judges` worksheet `rate_mpd` column.

### 2.2 Segment Size (AKC Show Manual §6)

A judge's breed assignment is divided into *segments* — contiguous blocks judged in a single
ring without interruption.

- **Soft cap:** ~1 hour of judging (25 dogs for standard, 20 for permit judges).
- **Hard cap:** 50 dogs maximum for any multi-breed segment.
- **Single-breed exception:** a breed with > 30 entries gets its own segment; no cap applies.
- **Equipment break:** open a new segment when equipment type changes and the soft cap is
  already met.

### 2.3 Mandatory Lunch Break (Hard Constraint)

Judges whose total breed judging time ≥ 300 minutes must receive a break of at least
45 minutes within the designated lunch window (typically 11:30 AM – 1:30 PM). The break must
occur between segments — it cannot interrupt a breed.

### 2.4 Sequencing (Hard Constraints)

- No judge may judge two events simultaneously.
- No ring may host two events simultaneously.
- Group judging for group *g* cannot begin until all breeds in *g* have completed BOB.
- BIS cannot begin until all seven groups have completed.

### 2.5 Judge Conflict Rules (Hard Constraints)

- A judge may not be assigned to both a breed and that breed's group.
- A judge may not be assigned to both a group and BIS.

### 2.6 Soft Lunch Availability

Judges not requiring a mandatory break should have at least one inter-segment gap during the
lunch window where they could reasonably eat. Modeled as a soft penalty.

---

## 3. Solution Approach

The solver backend is **OR-Tools CP-SAT** (`akc_cpsat.py`). All disjunctive scheduling
constraints are encoded using `AddNoOverlap` on interval variables — no big-M coefficients
are needed. This produces a compact, tightly-propagated model that typically finds a
near-optimal schedule within a few minutes for medium-sized shows.

### 3.1 Key Design Decisions

**No pre-designated arena rings.** All rings are available for breed segments. After solving,
`assign_arena_ring()` selects the ring whose last breed segment ends earliest as the arena
ring and sequences group and BIS events there sequentially. This eliminates the "vacate
arena" constraint from the optimizer and lets the objective naturally incentivise early
completion of breed judging.

**Segment-level scheduling.** The optimizer assigns *segments* (not individual breeds) to
rings and time slots. Breed order within a segment is fixed by preprocessing. This
significantly reduces problem size.

**Time-slot discretization.** Time is discretized into 5-minute slots. All durations and
start times are integer multiples of this slot. CP-SAT IntVar domains are bounded tightly
using critical-path lower bounds.

**Horizon.** `T = max_judge_load + Σ Dg[g] + D_BIS`. The `tau_bis` IntVar is lower-bounded
by `max(critical_path_lb, ceil(total_seg_slots / n_rings))`.

---

## 4. Preprocessing (`akc_preprocessing.py`)

### 4.1 Segment Packing

Breeds for each judge are sorted by `(equipment_order, n_total desc)` to cluster equipment
types and push large breeds to the front. They are then greedily packed into segments
following the size rules in §2.2. Equipment breaks open a new segment when equipment type
changes and the soft cap is already met.

**Resulting segment fields:**

| Field | Type | Description |
|---|---|---|
| `segment_id` | str | `SEG0001`, `SEG0002`, … |
| `judge_id` | str | owning judge |
| `breed_ids` | list[str] | breeds in judging order |
| `equipment_sequence` | list[str] | equipment per breed (table/ramp/floor) |
| `duration_slots` | int | `ceil(n_dogs * rate_mpd / slot_min)` |
| `n_dogs` | int | total entries |
| `has_equipment_mix` | bool | multiple equipment types in segment |

### 4.2 Time Model

- Slot duration: configurable (default 5 min).
- `T0` = absolute slot index when judging begins (e.g., 8:00 AM).
- `T` = total slots in the CP-SAT horizon (relative, 0-indexed).
- All model variables are relative to `T0`; add `T0` to convert to absolute slot.

### 4.3 Lunch Classification

- **Mandatory-break judges** (`judges_requiring_lunch`): total breed judging time ≥ 300 min.
  Get a hard lunch gap constraint (C12).
- **Soft-lunch judges**: all other multi-segment judges. Incur a penalty if no inter-segment
  gap falls in the lunch window (C13).

---

## 5. Mathematical Model (`akc_cpsat.py`)

### 5.1 Index Sets

| Symbol | Definition |
|---|---|
| S | all segment IDs |
| R | all ring IDs (all rings available to breed segments) |
| J | all judges |
| G | all AKC groups (Sporting, Hound, Working, Terrier, Toy, Non-Sporting, Herding) |
| SP | same-judge segment pairs `{(sa,sb) : sa < sb, same judge}` |
| XP | cross-judge segment pairs `S×S \ SP` |
| LG | mandatory-lunch gap pairs `{(j,i) : j ∈ J_break, 0 ≤ i < |segs(j)|-1}` |
| SG | soft-lunch gap pairs (same structure, `j ∈ J_soft`) |
| JG | judge-group pairs `{(j,g) : judge j judges group g}` |
| JG_SEG | `{(j,g,s) : (j,g) ∈ JG, s ∈ segs(j)}` |
| RS | ring-switch pairs `{(j,i) : judge j has ≥2 segments, 0≤i<|segs(j)|-1}` |

### 5.2 Parameters

| Symbol | Type | Description |
|---|---|---|
| `D[s]` | int | duration in slots of segment s |
| `Dg[g]` | int | duration in slots of group g judging |
| `D_BIS` | int | duration in slots of BIS (fixed, 20 min) |
| `T` | int | CP-SAT horizon |
| `T0` | int | absolute slot of judging start |
| `t_ls`, `t_le` | int | lunch window start/end (relative) |
| `L_slots` | int | mandatory break duration in slots |
| `lb[s]` | int | serial lower bound: sum of durations of all earlier segments of same judge |

### 5.3 Decision Variables

| Variable | CP-SAT implementation | Description |
|---|---|---|
| `u[s,r]` | `pres[sid][rid]` BoolVar | 1 if segment s in ring r |
| `start_s[s]` | `start[sid]` IntVar in `[lb[s], T−D[s]]` | start slot of segment s |
| `tau_g[g]` | `tau_g[gid]` IntVar | start slot of group g |
| `tau_bis` | `tau_bis` IntVar in `[bis_lb, T−D_BIS]` | BIS start slot |
| `ell[j]` | `lunch_start_var[jid]` IntVar in `[t_ls, t_le−L]` | mandatory lunch start |
| `sl_gap[j,i]` | `sl_active[jid][i]` BoolVar | soft-lunch gap qualifies |
| `lunch_pen[j]` | `lunch_pen[jid]` BoolVar | no qualifying soft-lunch gap |
| `z[j,i]` | `sw_vars[jid,i]` BoolVar | judge j switches rings at gap i |

Ordering variables (`ord`, `ord_rp`, `ord_arena`, `ord_jg`, `ord_bis`, `lam`) are
**eliminated** — replaced by `AddNoOverlap` on interval variables, which achieves equivalent
or tighter propagation without explicit binary variables.

### 5.4 Constraints

#### C1 — Ring assignment (one ring per segment)
```
Σ_r u[s,r] = 1    ∀s ∈ S
```
*CP-SAT: `AddExactlyOne(pres[sid].values())`*

#### C2 — Segment fits within judging window
Encoded as the upper bound on `start_s[s]`: `start_s[s] ≤ T − D[s]`.

#### C4 — Ring non-overlap
For each ring r, optional interval variables `(start[s], D[s], pres[s][r])` are collected and
passed to `AddNoOverlap`. Fires only when both segments are present in the same ring.

#### C5 — Judge sequencing
For each judge j, a fixed interval is added to the judge's `AddNoOverlap` timeline for each
segment. No two segments of the same judge can overlap.

#### C6 — Arena serialization (group events)
Group interval variables are added to a single arena `AddNoOverlap` timeline. Groups are
sequenced without overlap; the arena ring is chosen post-hoc.

#### C8 — Group waits for all BOBs
```
start_s[s] + D[s] ≤ tau_g[g]    ∀g, ∀s containing a breed in g
```
Direct precedence constraint; no big-M needed.

#### C9 — BIS waits for all groups
```
tau_g[g] + Dg[g] ≤ tau_bis    ∀g ∈ G
```

#### C10 — All events finish by end of day
Encoded as upper bounds on `tau_g[g]` and `tau_bis`.

#### C11 — Group/BIS judges don't overlap their breed segments
Group and BIS interval variables are added directly to the owning judge's `AddNoOverlap`
timeline, preventing breed and group/BIS segments from overlapping.

#### C12 — Mandatory lunch break (hard)
A fixed-duration lunch interval `(ell[j], L_slots)` is added to judge j's `AddNoOverlap`
timeline. `ell[j]` is constrained to `[t_ls, t_le − L_slots]`. The `AddNoOverlap` constraint
implicitly selects which inter-segment gap hosts the break.

#### C13 — Soft lunch availability (penalty)
For each soft-lunch judge j and gap i:

- **C13a:** gap qualifies only if segment i ends ≤ `t_le − L_slots`
  *(enforced via `OnlyEnforceIf`)*
- **C13b:** gap qualifies only if segment i+1 starts ≥ `t_ls`
  *(enforced via `OnlyEnforceIf`)*
- **C13c:** `lunch_pen[j] ≥ 1 − Σ_i sl_gap[j,i]`
  *(via `AddBoolOr`)*

#### C15 — Ring-switch indicator
For each consecutive segment pair `(i, i+1)` of judge j and each ring r, a `same_r` BoolVar
is constrained to be 1 only when both segments are in ring r:

```
sw[j,i] + Σ_r same_r[j,i,r] = 1
```

`sw[j,i] = 1` iff the two segments are in different rings.

Optional hard constraint (`SolveParams.forbid_ring_switches = True`): force all `sw[j,i] = 0`.

#### C16 — Symmetry breaking
Omitted — CP-SAT handles ring symmetry natively through its search.

---

## 6. Objective

Two-level weighted-epsilon hierarchy (single weighted sum):

```
minimize  w_L1 · tau_bis  +  w_L3 · (Σ_{(j,i)∈RS} z[j,i]  +  Σ_j lunch_pen[j])
```

Where:
- `w_L3 = slots(ring_switch_penalty_min)` if `SolveParams.ring_switch_penalty_min > 0`, else `1`
- `L3_max = |RS| + |J_soft| + 1`
- `w_L1 = L3_max · w_L3 + 1` — any 1-slot BIS improvement beats any L3 gain

**Rationale:** Finishing early (`tau_bis` small) is the primary goal. The secondary goal is
minimizing ring switches and soft lunch penalties. Setting `ring_switch_penalty_min` trades
BIS time for fewer ring switches; `forbid_ring_switches = True` bans them entirely.

---

## 7. Post-hoc Arena Assignment (`assign_arena_ring` in `akc_schedule.py`)

After solving, the arena ring is selected and group/BIS events are placed:

1. For each ring, find the slot when its last breed segment ends (`ring_last[r]`).
2. Choose `arena_ring = argmin_r ring_last[r]` (ring that frees up earliest).
3. Sequence group events in `arena_ring`, each starting at
   `max(tau_g[g], current_free_slot)`, in temporal order.
4. Place BIS immediately after the last group.

This eliminates the "vacate arena" constraint from the optimizer: the objective already
incentivises finishing breed judging early, and post-hoc assignment picks the ring that is
already free.

---

## 8. Output

### 8.1 Judging Program (`akc_program.py`)

Generates a text judging program listing, for each ring:

- Judge name and assignment
- Ordered breeds with approximate start times
- Lunch break annotations (mandatory-break judges: fixed time; soft-break judges: "Lunch at
  their discretion" printed between qualifying inter-segment gaps)

### 8.2 Schedule Visualization (`akc_viz.py`)

Generates an interactive Plotly HTML Gantt chart showing all segments, group events, and BIS
on a shared timeline.

---

## 9. Files

| File | Role |
|---|---|
| `akc_preprocessing.py` | Data loading, segment packing, `ShowData` dataclass |
| `akc_schedule.py` | Shared types: `SolveParams`, `SolveResult`, `SegmentSchedule`, `GroupSchedule`, `assign_arena_ring` |
| `akc_cpsat.py` | OR-Tools CP-SAT solver; `solve_show(show, params) -> SolveResult` |
| `akc_viz.py` | Interactive Plotly HTML schedule chart from `SolveResult` |
| `akc_program.py` | Text judging program generator from `SolveResult` |
| `akc_show_generator.py` | Synthetic show workbook generator for testing |
| `akc_cpsat_bench.py` | Benchmarking harness for CP-SAT solver |
| `CLAUDE.md` | Mathematical specification for Claude Code (keep in sync with source files) |

---

## 10. Running the Solver

```bash
# Solve and print the judging program
python3 akc_program.py small.xlsx

# Solve with a time limit and visualize
python3 akc_viz.py medium.xlsx --time-limit 120

# Benchmark constraint families
python3 akc_cpsat_bench.py small.xlsx
```

### Solver Parameters (`SolveParams`)

| Parameter | Default | Description |
|---|---|---|
| `solver` | `"cpsat"` | Solver backend |
| `time_limit_sec` | `300` | Wall-clock time limit |
| `gap` | `0.01` | Optimality gap tolerance (1%) |
| `threads` | `0` | Worker threads (0 = auto) |
| `tee` | `True` | Print solver progress |
| `ring_switch_penalty_min` | `0.0` | BIS minutes traded per ring switch (0 = tiebreaker only) |
| `forbid_ring_switches` | `False` | Hard-ban all ring switches |

---

## 11. Known Limitations and Deferred Features

- **Warm start / solution hints:** CP-SAT `AddHint()` greedy construction not yet implemented.
  The solver starts from scratch and relies on its own heuristics for the first incumbent.
- **Exhibitor conflict avoidance:** handlers showing multiple dogs in overlapping breeds are
  not yet modeled. Planned as a future soft-penalty term.
- **Search strategy:** `AddDecisionStrategy()` on `pres` variables not yet tuned.
