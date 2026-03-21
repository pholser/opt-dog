# AKC All-Breed Show Scheduling — MIP Model Specification

**Living document — keep in sync with `akc_preprocessing.py`, `akc_schedule.py`, and `akc_cpsat.py`**

---

## 1. Problem Description

An AKC all-breed dog show assigns each entered breed to a judge; the show committee fixes which judge judges which breeds before the optimizer runs.  The optimizer decides:

1. Which physical ring each judge's *segment* (block of breeds) occupies, and
2. When each segment starts.

The goal is to finish the show as early as possible (minimize Best-in-Show start time), subject to hard scheduling constraints and with a secondary penalty for ring-switch friction.

---

## 2. Preprocessing (`akc_preprocessing.py`)

### 2.1 Judging rates

| Judge type | Rate | Source |
|---|---|---|
| Standard | 2.4 min/dog (25 dogs/hr) | AKC Show Manual §6 |
| Permit    | 3.0 min/dog (20 dogs/hr) | AKC Show Manual §6 |

Per-judge override supported via the `Judges` worksheet `rate_mpd` column.

### 2.2 Segment packing (`_pack_segments`)

Each judge's breed assignments are divided into *segments* — contiguous blocks judged in a single ring without interruption.

**Rules (AKC Show Manual §6):**

- **Soft cap**: `floor(60 / rate_mpd)` dogs ≈ 1 hour.  Standard → 25; Permit → 20.
- **Hard cap**: 50 dogs for *multi-breed* segments.  Never exceeded.
- **Single-breed exception**: a breed with > 30 entries gets its own dedicated segment; no cap applies (AKC: "except where entry in a breed/variety exceeds 30").
- **Equipment break**: open a new segment when the equipment type changes *and* we are already at or past the soft cap.
- Breeds sorted within a judge's assignment by `(equipment_order, n_total desc)` to cluster equipment types and push large breeds to the front of each group.

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

### 2.3 Time model

- Slot duration: configurable (default 5 min).
- `T0` = absolute slot index when judging begins (e.g., 8:00 AM).
- `T` = total slots in judging window (relative, 0-indexed).
- All model time variables are *relative* to `T0`; add `T0` to convert to absolute slot.

### 2.4 Lunch rules

- Judges with total breed judging time ≥ 300 min are *mandatory-break* judges (`judges_requiring_lunch`).  They get a hard lunch gap constraint.
- All other multi-segment judges are *soft-lunch* judges.  A penalty is incurred if no inter-segment gap falls in the lunch window.
- Lunch window: `[t_ls, t_le]` (relative).  Break duration: `L_slots`.

---

## 3. Mathematical Model (`akc_cpsat.py`)

### 3.1 Index sets

| Symbol | Definition |
|---|---|
| S | all segment IDs |
| R | all ring IDs (all rings available to breed segments; no pre-designated arena rings) |
| J | all judges |
| G | all groups (Sporting, Hound, …, Herding) |
| SP | same-judge segment pairs `{(sa,sb) : sa < sb, same judge}` |
| XP | cross-judge segment pairs `S×S \ SP` (for ring non-overlap) |
| LG | mandatory-lunch gap pairs `{(j,i) : j ∈ J_break, 0 ≤ i < |segs(j)|-1}` |
| SG | soft-lunch gap pairs (same structure, `j ∈ J_soft`) |
| JG | judge-group pairs `{(j,g) : judge j judges group g}` |
| JG_SEG | `{(j,g,s) : (j,g) ∈ JG, s ∈ segs(j)}` |
| RS | ring-switch pairs `{(j,i) : judge j has ≥2 segments, 0≤i<|segs(j)|-1}` |

**Note on arena rings:** There are no pre-designated arena rings in the optimizer.  All rings are available for breed segments.  After solving, `_assign_arena_ring()` selects the ring whose last breed segment ends earliest as the arena ring, and sequences group/BIS events there.  See §3.5 C6 and §3.7.

### 3.2 Parameters

| Symbol | Type | Description |
|---|---|---|
| `D[s]` | int | duration in slots of segment s |
| `Dg[g]` | int | duration in slots of group g judging |
| `D_BIS` | int | duration in slots of BIS (fixed, 20 min) |
| `T` | int | total slots in judging window |
| `T0` | int | absolute slot of judging start |
| `t_ls`, `t_le` | int | lunch window start/end (relative) |
| `L_slots` | int | mandatory break duration in slots |
| `lb[s]` | int | serial lower bound on `start_s[s]`: sum of durations of all earlier segments of the same judge |

### 3.3 Decision variables

| Variable | Abstract domain | CP-SAT implementation |
|---|---|---|
| `u[s,r]` | {0,1} | `pres[sid][rid]` BoolVar |
| `start_s[s]` | ℤ≥0, ≤T−D[s] | `start[sid]` IntVar |
| `ord[sa,sb]` | {0,1} | **eliminated** — replaced by `AddNoOverlap` on judge timeline |
| `ord_rp[s1,s2]` | {0,1} | **eliminated** — replaced by `AddNoOverlap` on ring optional intervals |
| `ord_arena[g1,g2]` | {0,1} | **eliminated** — replaced by `AddNoOverlap` on arena timeline |
| `ord_jg[j,g,s]` | {0,1} | **eliminated** — group intervals added to judge `AddNoOverlap` |
| `ord_bis[s]` | {0,1} | **eliminated** — BIS interval added to BIS judge `AddNoOverlap` |
| `tau_g[g]` | ℤ≥0, ≤T−Dg[g] | `tau_g[gid]` IntVar |
| `tau_bis` | ℤ≥0, [bis_lb, T−D_BIS] | `tau_bis` IntVar; lower-bounded by critical-path/load-balance |
| `ell[j]` | ℤ, [t_ls, t_le−L] | `lunch_start_var[jid]` IntVar; lunch interval in judge `AddNoOverlap` |
| `lam[j,i]` | {0,1} | **eliminated** — `AddNoOverlap` implicitly selects the gap |
| `sl_gap[j,i]` | {0,1} | `sl_active[jid][i]` BoolVar |
| `lunch_pen[j]` | {0,1} | `lunch_pen[jid]` BoolVar |
| `z[j,i]` | {0,1} | `sw_vars[jid,i]` BoolVar; `same_r_vars` BoolVars for each ring |
| `f_ring[r]` | ℝ≥0, ≤T | **omitted** — CP-SAT handles symmetry natively |

**CP-SAT horizon `T`:** computed as `max_judge_load + Σ Dg[g] + D_BIS` (critical-path upper bound), not a fixed show-window length.  `tau_bis` is lower-bounded by `max(max_judge_load, ceil(Σ D[s] / |R|))`.

### 3.4 Valid bounds *(MIP-specific)*

The serial lower bound `lb[s] = Σ_{k<i} D[seg_k(j)]` is used in the MIP to tighten big-M values in disjunctive constraints (C4, C5, C11).  CP-SAT does not use big-M; `AddNoOverlap` propagation achieves equivalent or tighter pruning without explicit bounds.

### 3.5 Constraints

#### C1 — Ring assignment (one ring per segment)
```
Σ_r u[s,r] = 1    ∀s ∈ S
```

#### C2 — Segment fits within judging window
Encoded as upper bound on `start_s[s]`: `start_s[s] ≤ T − D[s]`.  No explicit constraint.

#### C4 — Ring non-overlap (disjunctive, cross-judge pairs)

For each `(s1,s2) ∈ XP` and ring `r`:

```
start_s[s1] + D[s1] ≤ start_s[s2] + M4a · (1 − ord_rp[s1,s2]) + M4a · (2 − u[s1,r] − u[s2,r])
start_s[s2] + D[s2] ≤ start_s[s1] + M4b · ord_rp[s1,s2]         + M4b · (2 − u[s1,r] − u[s2,r])
```

Where `M4a = T − lb[s2]`, `M4b = T − lb[s1]` (tightened using serial lower bounds).

Only fires when both segments are in the same ring.

#### C5 — Judge sequencing (same-judge pairs)

For each `(sa,sb) ∈ SP`:

```
start_s[sa] + D[sa] ≤ start_s[sb] + (T − lb[sb]) · (1 − ord[sa,sb])   (C5a)
start_s[sb] + D[sb] ≤ start_s[sa] + (T − lb[sa]) · ord[sa,sb]          (C5b)
```

Exactly one of sa, sb runs first; this encodes the disjunction.

#### C6 — Arena serialization (group events)

Group and BIS events are scheduled as pure temporal variables (`tau_g`, `tau_bis`) with no ring assignment in the optimizer.  C6 serializes group events:

For each `(g1,g2) ∈ GG_PAIRS`:

```
tau_g[g1] + Dg[g1] ≤ tau_g[g2] + (T−Dg[g1]) · (1 − ord_arena[g1,g2])   (C6a_fwd)
tau_g[g2] + Dg[g2] ≤ tau_g[g1] + (T−Dg[g2]) · ord_arena[g1,g2]           (C6a_rev)
```

The arena ring is chosen post-hoc (see §3.7); C7 is eliminated by this design.

#### C8 — Group waits for all BOBs

```
start_s[s] + D[s] ≤ tau_g[g]    ∀g, ∀s containing a breed in g    (C8a)
```
(No big-M; direct precedence constraint.)

#### C9 — BIS waits for all groups

```
tau_g[g] + Dg[g] ≤ tau_bis    ∀g ∈ G    (C9)
```

#### C10 — All events finish by end of day
Encoded as upper bounds: `tau_g[g] ≤ T − Dg[g]`, `tau_bis ≤ T − D_BIS`.  No explicit constraints.

#### C11 — Group/BIS judges don't overlap their breed segments

For each `(j,g,s) ∈ JG_SEG`:

```
start_s[s] + D[s] ≤ tau_g[g] + (T−D[s])    · (1 − ord_jg[j,g,s])   (C11a)
tau_g[g]   + Dg[g] ≤ start_s[s] + (T−lb[s]) · ord_jg[j,g,s]          (C11b)
```

For each `s ∈ BIS_SEGS` (segments of BIS judge):

```
start_s[s] + D[s] ≤ tau_bis + (T−D[s])    · (1 − ord_bis[s])   (C11c)
tau_bis + D_BIS   ≤ start_s[s] + (T−lb[s]) · ord_bis[s]          (C11d)
```

#### C12 — Mandatory lunch break (hard constraint)

For each mandatory-break judge j:

**C12a** — exactly one gap is the lunch gap:
```
Σ_i lam[j,i] = 1
```

**C12b** — lunch starts after segment i ends (if gap i is the lunch gap):
```
ell[j] ≥ start_s[seg_i(j)] + D[seg_i(j)] − (T−t_ls) · (1 − lam[j,i])
```

**C12c** — segment i+1 starts after lunch ends:
```
start_s[seg_{i+1}(j)] ≥ ell[j] + L_slots − t_le · (1 − lam[j,i])
```

**C12d/C12e** — lunch window encoded as variable bounds:
```
ell[j] ∈ [t_ls, t_le − L_slots]
```

#### C13 — Soft lunch availability (penalty)

For each soft-lunch judge j, gap i:

**C13a** — gap qualifies only if segment i ends ≤ t_le − L_slots:
```
start_s[seg_i(j)] + D[seg_i(j)] ≤ (t_le − L_slots) + (T−(t_le−L_slots)) · (1 − sl_gap[j,i])
```

**C13b** — gap qualifies only if segment i+1 starts ≥ t_ls:
```
start_s[seg_{i+1}(j)] ≥ t_ls − t_ls · (1 − sl_gap[j,i])
```

**C13c** — penalty fires if no gap qualifies:
```
lunch_pen[j] ≥ 1 − Σ_i sl_gap[j,i]
```

#### C15 — Ring-switch indicator

For each `(j,i) ∈ RS`:

```
z[j,i] ≥ u[seg_i(j), r] − u[seg_{i+1}(j), r]    ∀r ∈ R   (C15a)
z[j,i] ≥ u[seg_{i+1}(j), r] − u[seg_i(j), r]    ∀r ∈ R   (C15b)
```

`z[j,i] = 1` iff segments i and i+1 of judge j are in different rings.

Optional hard constraint (`SolveParams.forbid_ring_switches = True`): `z[j,i] = 0 ∀(j,i) ∈ RS`.

#### C16 — Symmetry breaking (ring activation order) — *not yet implemented*

Variable `f_ring[r]` is declared for each breed ring but the constraints are not yet added:

```
f_ring[r] ≤ start_s[s] + T · (1 − u[s,r])    ∀s ∈ S    (C16a, pending)
f_ring[r_k] ≤ f_ring[r_{k+1}]                            (C16b, pending)
```

CP-SAT omits C16 entirely — symmetry is handled natively by the solver.

### 3.6 Objective

Two-level weighted-epsilon hierarchy (single weighted sum):

```
minimize  w_L1 · tau_bis  +  w_L3 · (Σ_{(j,i)∈RS} z[j,i]  +  Σ_j lunch_pen[j])
```

Where:
- `w_L3 = slots(ring_switch_penalty_min)` if `SolveParams.ring_switch_penalty_min > 0`, else `1`
- `L3_max = |RS| + |J_soft| + 1`
- `w_L1 = L3_max · w_L3 + 1` — any 1-slot BIS improvement beats any L3 gain

**Rationale:** Finishing early (small `tau_bis`) is the primary goal.  Secondary goal is minimizing ring switches and soft lunch penalties.  `ring_switch_penalty_min` makes each switch cost that many minutes of BIS time; `forbid_ring_switches` bans switches entirely.

### 3.7 Post-hoc arena ring assignment (`_assign_arena_ring`)

After solving, the arena ring is selected and group/BIS events are placed:

1. For each ring, find the slot when its last breed segment ends (`ring_last[r]`).
2. Choose `arena_ring = argmin_r ring_last[r]` (ring that frees up earliest).
3. Sequence group events in `arena_ring` starting at `max(tau_g[g], current_free_slot)` for each group in temporal order.
4. Place BIS after the last group.

This eliminates C7 (breed segments vacate arena) as a model constraint: the optimizer minimizes BIS time, which naturally incentivises finishing breed judging early, and post-hoc assignment picks the ring that is already free.

---

## 4. Big-M Summary *(MIP formulation only — not applicable to CP-SAT)*

All big-M values use the tightest valid bound.  `lb[s]` is the serial lower bound on `start_s[s]` (cumulative duration of earlier segments for the same judge).

| Constraint | Big-M expression | Justification |
|---|---|---|
| C4a | `T − lb[s2]` | `start_s[s2] ≥ lb[s2]` |
| C4b | `T − lb[s1]` | `start_s[s1] ≥ lb[s1]` |
| C5a | `T − lb[sb]` | `start_s[sb] ≥ lb[sb]` |
| C5b | `T − lb[sa]` | `start_s[sa] ≥ lb[sa]` |
| C6a_fwd | `T − Dg[g1]` | `tau_g[g1] ≤ T − Dg[g1]` |
| C6a_rev | `T − Dg[g2]` | same |
| C11a/C11c | `T − D[s]` | `start_s[s] ≤ T − D[s]` |
| C11b/C11d | `T − lb[s]` | `start_s[s] ≥ lb[s]` |
| C12b | `T − t_ls` | `ell[j] ≥ t_ls` always |
| C12c | `t_le` | `ell[j] + L ≤ t_le` always |
| C13a | `T − (t_le − L_slots)` | segment end ≤ T |
| C13b | `t_ls` | segment start ≥ 0 |
| C16a | `T` | cannot be tightened (pending) |

---

## 5. Solution Hints *(CP-SAT — not yet implemented)*

CP-SAT accepts variable hints via `model.AddHint(var, value)` to seed the search with a greedy feasible solution, reducing time-to-first-incumbent.  A greedy construction heuristic is planned:

1. For each judge in order, place each segment at the earliest available slot across all rings, inserting an `L`-slot gap for mandatory-break judges when the previous segment ends at or after `t_ls`.
2. Set `start[sid]`, `pres[sid][rid]`, `tau_g[gid]`, `tau_bis`, and lunch/switch variables from the greedy placement.

Until implemented, CP-SAT starts from scratch and relies on its own internal heuristics for the first incumbent.

---

## 6. Solution Extraction

`solver.Value()` reads integer variable values from the CP-SAT solution.  `assign_arena_ring()` (from `akc_schedule`) is then called to select the arena ring and place group/BIS events, producing the final `SolveResult`.

---

## 7. Files

| File | Role |
|---|---|
| `akc_preprocessing.py` | Data loading, segment packing, `ShowData` dataclass |
| `akc_schedule.py` | Shared types: `SolveParams`, `SolveResult`, `SegmentSchedule`, `GroupSchedule`, `assign_arena_ring` |
| `akc_cpsat.py` | OR-Tools CP-SAT solver; `solve_show(show, params) -> SolveResult` |
| `akc_viz.py` | Interactive Plotly HTML schedule chart from `SolveResult` |
| `akc_program.py` | Text judging program generator from `SolveResult` |
| `akc_show_generator.py` | Synthetic show workbook generator for testing |
| `akc_cpsat_bench.py` | Benchmarking harness for CP-SAT solver |
| `CLAUDE.md` | **This file** — mathematical specification (keep in sync) |

---

## 8. Implementation Status

| Item | CP-SAT (`akc_cpsat.py`) |
|---|---|
| C1 ring assignment | ✓ `AddExactlyOne` |
| C2 window (as variable bound) | ✓ IntVar upper bound |
| C4 ring non-overlap | ✓ `AddNoOverlap` on optional ring intervals |
| C5 same-judge sequencing | ✓ `AddNoOverlap` on judge timeline |
| C6 arena serialization | ✓ `AddNoOverlap` on arena timeline |
| C7 vacate arena | eliminated — arena ring chosen post-hoc |
| C8 group waits for BOBs | ✓ direct `Add(end_s <= tau_g)` |
| C9 BIS waits for groups | ✓ direct `Add(end_g <= tau_bis)` |
| C10 end-of-day | ✓ IntVar upper bounds |
| C11 judge/group/BIS non-overlap | ✓ group/BIS intervals added to judge `AddNoOverlap` |
| C12 mandatory lunch | ✓ lunch IntervalVar in judge `AddNoOverlap` |
| C13 soft lunch penalty | ✓ `OnlyEnforceIf` + `AddBoolOr` |
| C15 ring-switch indicator | ✓ `same_r` BoolVars + `sw + Σsame_r == 1` |
| C16 symmetry breaking | omitted — CP-SAT handles natively |
| Solution hints (warm start) | pending |
| Objective | ✓ `w_L1·tau_bis + w_L3·(Σsw + Σpen)` |
| Post-hoc arena assignment | ✓ `assign_arena_ring()` in `akc_schedule.py` |
