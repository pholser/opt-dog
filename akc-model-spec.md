# AKC All-Breed Show Scheduling — MIP Model Specification

**Living document — keep in sync with `akc_preprocessing.py` and `akc_mip2.py`**

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

## 3. Mathematical Model (`akc_mip2.py`)

### 3.1 Index sets

| Symbol | Definition |
|---|---|
| S | all segment IDs |
| R | all ring IDs |
| R_breed | non-group rings |
| R_arena | group/BIS rings (share one logical arena) |
| J | all judges |
| G | all groups (Sporting, Hound, …, Herding) |
| SP | same-judge segment pairs `{(sa,sb) : sa < sb, same judge}` |
| XP | cross-judge segment pairs `S×S \ SP` (for ring non-overlap) |
| LG | mandatory-lunch gap pairs `{(j,i) : j ∈ J_break, 0 ≤ i < |segs(j)|-1}` |
| SG | soft-lunch gap pairs (same structure, `j ∈ J_soft`) |
| JG | judge-group pairs `{(j,g) : judge j judges group g}` |
| JG_SEG | `{(j,g,s) : (j,g) ∈ JG, s ∈ segs(j)}` |
| RS | ring-switch pairs `{(j,i) : judge j has ≥2 segments, 0≤i<|segs(j)|-1}` |

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

### 3.3 Decision variables

| Variable | Domain | Description |
|---|---|---|
| `u[s,r]` | {0,1} | 1 if segment s assigned to ring r |
| `start_s[s]` | ℝ≥0, ≤T | start slot of segment s |
| `ord[sa,sb]` | {0,1} | 1 if sa scheduled before sb (same judge) |
| `ord_rp[s1,s2]` | {0,1} | 1 if s1 before s2 (cross-judge, ring non-overlap) |
| `ord_arena[g1,g2]` | {0,1} | 1 if group g1 runs before g2 in arena |
| `ord_jg[j,g,s]` | {0,1} | 1 if breed segment s before group g (judge j) |
| `ord_bis[s]` | {0,1} | 1 if breed segment s before BIS (BIS judge only) |
| `tau_g[g]` | ℝ≥0, ≤T | group g start slot |
| `tau_bis` | ℝ≥0, ≤T | BIS start slot |
| `ell[j]` | ℝ, [t_ls, t_le − L] | mandatory lunch start slot for judge j |
| `lam[j,i]` | {0,1} | 1 if gap i is the mandatory lunch gap for judge j |
| `sl_gap[j,i]` | {0,1} | 1 if soft-lunch gap i qualifies for judge j |
| `lunch_pen[j]` | {0,1} | 1 if no qualifying soft-lunch gap for judge j |
| `z[j,i]` | {0,1} | 1 if judge j switches rings between segment i and i+1 |
| `f_ring[r]` | ℝ≥0, ≤T | earliest segment start in ring r (symmetry breaking) |

### 3.4 Valid bounds (applied before solving)

Applied via `setlb()` — tighten LP relaxation without removing feasible solutions.

**Per-segment start lower bounds:**
```
start_s[seg_i(j)] ≥ Σ_{k<i} D[seg_k(j)]   for each judge j, segment index i
```
Each segment must start after all earlier segments of the same judge have finished (serial lower bound).

**tau_g lower bounds:**
```
tau_g[g] ≥ max_{j feeds g} Σ_{s ∈ segs(j)} D[s]
```
Group g cannot start until all segments of every judge feeding it are done.

**tau_bis lower bound:**
```
tau_bis ≥ max_j Σ_{s ∈ segs(j)} D[s]
```

### 3.5 Constraints

#### C1 — Ring assignment (one ring per segment)
```
Σ_r u[s,r] = 1    ∀s ∈ S
```

#### C2 — Segment fits within judging window
```
start_s[s] + D[s] ≤ T    ∀s ∈ S
```

#### C4 — Ring non-overlap (disjunctive, cross-judge pairs)

For each `(s1,s2) ∈ XP` and ring `r`:

```
start_s[s1] + D[s1] ≤ start_s[s2] + M4a[s1] · (1 − ord_rp[s1,s2]) + M4a[s1] · (2 − u[s1,r] − u[s2,r])
start_s[s2] + D[s2] ≤ start_s[s1] + M4b[s2] · ord_rp[s1,s2]         + M4b[s2] · (2 − u[s1,r] − u[s2,r])
```

Where `M4a[s1] = T − D[s1]`, `M4b[s2] = T − D[s2]` (per-segment tight big-M).

Only fires when both segments are in the same ring.

#### C5 — Judge sequencing (same-judge pairs)

For each `(sa,sb) ∈ SP`:

```
start_s[sa] + D[sa] ≤ start_s[sb] + (T−D[sa]) · (1 − ord[sa,sb])   (C5a)
start_s[sb] + D[sb] ≤ start_s[sa] + (T−D[sb]) · ord[sa,sb]          (C5b)
```

Exactly one of sa, sb runs first; this encodes the disjunction.

#### C6 — Arena serialization (group events)

For each `(g1,g2) ∈ GG_PAIRS`:

```
tau_g[g1] + Dg[g1] ≤ tau_g[g2] + (T−Dg[g1]) · (1 − ord_arena[g1,g2])   (C6a_fwd)
tau_g[g2] + Dg[g2] ≤ tau_g[g1] + (T−Dg[g2]) · ord_arena[g1,g2]           (C6a_rev)
```

#### C7 — Breed segments vacate arena before group judging

For each `s ∈ S`, `g ∈ G`:

```
start_s[s] + D[s] ≤ tau_g[g] + (T−D[s]) · (1 − Σ_{r ∈ R_arena} u[s,r])   (C7a)
start_s[s] + D[s] ≤ tau_bis  + (T−D[s]) · (1 − Σ_{r ∈ R_arena} u[s,r])   (C7b)
```

#### C8 — Group waits for all BOBs

```
start_s[s] + D[s] ≤ tau_g[g]    ∀g, ∀s containing a breed in g    (C8a)
```
(No big-M; direct precedence constraint, since this must *always* hold.)

#### C9 — BIS waits for all groups

```
tau_g[g] + Dg[g] ≤ tau_bis    ∀g ∈ G    (C9)
```

#### C10 — All events finish by end of day

```
tau_g[g]  + Dg[g]  ≤ T    ∀g
tau_bis   + D_BIS  ≤ T
```

#### C11 — Group/BIS judges don't overlap their breed segments

For each `(j,g,s) ∈ JG_SEG`:

```
start_s[s] + D[s] ≤ tau_g[g] + (T−D[s])    · (1 − ord_jg[j,g,s])   (C11a)
tau_g[g]   + Dg[g] ≤ start_s[s] + (T−Dg[g]) · ord_jg[j,g,s]         (C11b)
```

For each `s ∈ BIS_SEGS` (segments of BIS judge):

```
start_s[s] + D[s] ≤ tau_bis + (T−D[s])    · (1 − ord_bis[s])   (C11c)
tau_bis + D_BIS   ≤ start_s[s] + (T−D_BIS) · ord_bis[s]          (C11d)
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

**C12d/C12e** — lunch window:
```
ell[j] ≥ t_ls
ell[j] + L_slots ≤ t_le
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

#### C16 — Symmetry breaking (ring activation order)

For each non-group ring r, auxiliary variable `f_ring[r]` approximates the earliest start time in r:

```
f_ring[r] ≤ start_s[s] + T · (1 − u[s,r])    ∀s ∈ S    (C16a)
f_ring[r_k] ≤ f_ring[r_{k+1}]                            (C16b, for consecutive ring indices)
```

This breaks the symmetry among rings that have identical structure.

### 3.6 Objective

Two-level weighted-epsilon hierarchy (single weighted sum):

```
minimize  w_L1 · tau_bis  +  w_L3 · (Σ_{(j,i)∈RS} z[j,i]  +  Σ_j lunch_pen[j])
```

Where:
- `w_L1 = L3_max + 1` — any 1-slot BIS improvement beats any L3 gain
- `w_L3 = 1`
- `L3_max = |RS| + |J_soft| + 1` — upper bound on L3 objective

**Rationale:** Finishing early (small `tau_bis`) is the primary goal.  Secondary goal is minimizing ring switches (judge friction) and soft lunch penalties (exhibitor experience).  Equipment switches within a segment are a preprocessing concern, not an objective term — they are minimized by the segment packing algorithm.

---

## 4. Big-M Summary

All big-M values are the *tightest valid* bound derivable from variable domains.  No global `T` is used where a tighter value exists.

| Constraint | Big-M expression | Justification |
|---|---|---|
| C4a | `T − D[s1]` | `start_s[s1] ≤ T − D[s1]` |
| C4b | `T − D[s2]` | `start_s[s2] ≤ T − D[s2]` |
| C5a | `T − D[sa]` | same |
| C5b | `T − D[sb]` | same |
| C6a_fwd | `T − Dg[g1]` | `tau_g[g1] ≤ T − Dg[g1]` |
| C6a_rev | `T − Dg[g2]` | same |
| C7a/C7b | `T` | max(start_s+D[s])=T, min(tau_g/tau_bis)=0; T−D[s] is insufficient |
| C11a/C11c | `T − D[s]` | per-segment |
| C11b | `T − Dg[g]` | per-group |
| C11d | `T − D_BIS` | BIS duration fixed |
| C12b | `T − t_ls` | `ell[j] ≥ t_ls` always |
| C12c | `t_le` | `ell[j] + L ≤ t_le` always |
| C13a | `T − (t_le − L_slots)` | segment end ≤ T |
| C13b | `t_ls` | segment start ≥ 0 |
| C16a | `T` | cannot be tightened |

---

## 5. Warm Start (`_inject_greedy_warm_start`)

A greedy feasible solution is constructed and injected as a MIP start:

1. **Breed segments**: for each judge (sorted by ID), for each segment in order:
   - For mandatory-break judges: when the previous segment ended at or after `t_ls`, force a `L_slots` gap before placing the next segment (records `greedy_lunch[jid]`).
   - Assign to the earliest available slot across all breed rings.

2. **Group events**: assign sequentially in arena, each group starting after its last BOB segment finishes and the arena is free.

3. **BIS**: starts after the last group ends.

4. **Binary variables** set from placement:
   - `u[s,r]`: 1 for the assigned ring.
   - `ord[sa,sb]`: from relative start times.
   - `ord_rp`, `ord_arena`, `ord_jg`, `ord_bis`: same.
   - `z[j,i]`: ring switch indicator.
   - `lam[j,i]`: set using `m._judge_segs` ordering to guarantee C12b/C12c consistency.
   - `ell[j]`: set to end of the segment before the lunch gap, clamped to `[t_ls, t_le−L]`.
   - `sl_gap[j,i]` / `lunch_pen[j]`: set based on whether each greedy gap satisfies C13a/C13b.

5. **Fallback**: unset variables with `lb > 0` are set to their lower bound (for `ONE_VAR_CONSTANT` etc.), *skipping* variables already explicitly set.

All explicit assignments go through `_sv_set()` which registers the variable name in `_set_vars`, preventing the lb-fill from overwriting them.

---

## 6. Solution Extraction (`_build_scip_var_map`)

SCIP variable names differ from Pyomo names due to bracket→parenthesis mangling (e.g., `u[SEG0001,'1']` → `u(SEG0001__1_)`).  `_build_scip_var_map()` queries `scip.getVars()` directly and matches to Pyomo `VarData` objects by normalising both names to a canonical delimiter-free form: all `[](),' "` characters are replaced with `_` and runs of underscores are collapsed.  This is robust to all known Pyomo LP-writer mangling variants.  Pyomo variables that are not yet referenced in any active constraint are absent from the SCIP model and are expected to be unmatched during incremental model development.

---

## 7. Files

| File | Role |
|---|---|
| `akc_preprocessing.py` | Data loading, segment packing, `ShowData` dataclass |
| `akc_mip2.py` | Pyomo model, SCIP solver integration, warm start, solution extraction |
| `akc_program.py` | Text judging program generator from `SolveResult` |
| `MODEL_SPEC.md` | **This file** — mathematical specification (keep in sync) |

---

## 8. Constraint Checklist for Rebuild

When rebuilding the model, implement and test one constraint family at a time.  After each addition, verify:

- [ ] Model builds without error
- [ ] LP relaxation bound is reasonable (not trivially 0 or ∞)
- [ ] A small feasible example satisfies the constraint
- [ ] Big-M value matches the table in §4

**Recommended order:**

1. C1, C2 (assignment + window) — basic feasibility
2. C5 (judge sequencing) — core ordering
3. C4 (ring non-overlap) — ring resource constraint
4. C8, C9 (group/BIS precedence) — no big-M, clean constraints
5. C10 (end-of-day)
6. C6, C7 (arena serialization + vacate) — group arena logic
7. C11 (judge doesn't overlap own group/BIS)
8. C12 (mandatory lunch)
9. C13 (soft lunch penalty)
10. C15 (ring-switch cost)
11. C16 (symmetry breaking)
12. Valid bounds / warm start
13. Objective
