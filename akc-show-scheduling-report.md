# Automated Scheduling for AKC All-Breed Conformation Shows
## A Mixed-Integer Programming Approach

**Status:** Implementation Complete — Warm Start Pending
**Phases Complete:** Data Model, Mathematical Formulation, Synthetic Dataset Generator, Preprocessing Pipeline, MIP Solver (`akc_mip2.py` clean rebuild), Text Program Generator
**Current Work:** Warm start implementation; solver performance benchmarking on `akc_mip2.py`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Background and Motivation](#2-background-and-motivation)
3. [The Scheduling Problem](#3-the-scheduling-problem)
4. [AKC Rules and Constraints](#4-akc-rules-and-constraints)
5. [Mathematical Formulation](#5-mathematical-formulation)
6. [Data Model](#6-data-model)
7. [Output Format](#7-output-format)
8. [Implementation Architecture](#8-implementation-architecture)
9. [Design Decisions and Tradeoffs](#9-design-decisions-and-tradeoffs)
10. [Open Items and Deferred Features](#10-open-items-and-deferred-features)

---

## 1. Executive Summary

AKC all-breed conformation dog shows are complex multi-resource scheduling problems. A typical show involves 150–200 breeds competing across 8–12 judging rings over a single day, with a panel of 10–25 judges each assigned to specific breeds, a group, or Best in Show. The official output — called the **judging program** — specifies, for every ring, the sequence of breeds and their approximate start times.

Today this program is produced manually by experienced show superintendents. The process is labor-intensive, difficult to revise, and produces schedules of variable quality with respect to show duration, handler convenience, and resource utilization.

This project develops an **automated scheduling system** based on mixed-integer programming (MIP). The system reads a structured show input workbook, applies AKC rules and exhibitor constraints, and produces an optimized judging program as its output.

**Primary objective:** minimize the time at which Best in Show judging can begin — a proxy for total show duration, which directly affects exhibitor satisfaction and venue costs.

**Secondary objective** (lexicographically subordinate): minimize operational friction — judge ring transitions and soft lunch availability penalties.

The system targets the SCIP solver via pyscipopt, with the mathematical model expressed using Pyomo for solver-agnosticism.

> **Design note:** Exhibitor conflict avoidance (originally planned as a second-level objective) has been deferred; see §9.3 and §10.2. Equipment-switching penalties are handled entirely in preprocessing (segment packing); they are not an MIP objective term.

---

## 2. Background and Motivation

### 2.1 AKC All-Breed Conformation Shows

An AKC all-breed conformation dog show is a single-day event in which purebred dogs compete for the title of Best in Show (BIS). Competition proceeds in three stages:

**Breed judging.** Within each breed, dogs compete by sex and class (class dogs → class bitches → specials, i.e., champions). The breed judge awards Winners Dog, Winners Bitch, and Best of Breed (BOB).

**Group judging.** The seven BOB winners from each AKC group (Sporting, Hound, Working, Terrier, Toy, Non-Sporting, Herding) compete before a group judge. Group judging cannot begin until all breed judging within that group is complete.

**Best in Show.** The seven group winners compete before the BIS judge. BIS cannot begin until all group judging is complete.

A typical large all-breed show may involve:
- 175–200 AKC breeds and varieties
- 1,500–4,000 individual dog entries
- 8–15 judging rings operating simultaneously
- 12–30 judges, each assigned to one or more breeds plus optionally a group
- 7–9 hours of judging, beginning at 8–9 AM and targeting completion by late afternoon

### 2.2 The Judging Program

The show secretary or superintendent publishes a **judging program** prior to the show date. This document specifies, for each ring:

- The judge assigned to that ring
- The ordered list of breeds to be judged, with approximate start times
- Lunch breaks
- Cross-references when a judge moves between rings

Exhibitors use the judging program to plan their day — knowing when and where to present their dogs. Errors or inefficiencies in the program (breeds scheduled in conflict, excessive show length, handlers unable to show multiple dogs) create problems for exhibitors and the club.

### 2.3 Why Automate?

Manual scheduling has several limitations:

- **Labor intensity.** An experienced superintendent may spend several hours producing and revising the program.
- **Suboptimality.** Manual schedules rarely minimize show duration or exhibitor conflicts; they simply satisfy the hard constraints.
- **Fragility.** Late entry changes or judge substitutions require manual rescheduling.
- **Inconsistency.** Schedule quality varies with the skill and experience of the individual doing it.

A well-designed automated system can produce optimal or near-optimal schedules in minutes, explore tradeoffs that are impractical to analyze manually, and respond instantly to changes.

---

## 3. The Scheduling Problem

### 3.1 High-Level Structure

The scheduling problem has three interleaved layers:

**Layer 1 — Breed Judging.** Assign each breed to a ring and a start time. Breeds assigned to the same judge must be sequenced without overlap (one judge cannot be in two places). Breeds in the same ring must be sequenced without overlap (one ring cannot host two events simultaneously).

**Layer 2 — Group Judging.** Each of the seven groups can begin only after all breeds in that group have completed BOB. Group judging occurs in a designated subset of rings ($R^G$), which may also host breed judging earlier in the day but must be free when group judging begins.

**Layer 3 — Best in Show.** BIS can begin only after all seven groups have completed. BIS also occurs in a group ring.

The coupling between layers creates a complex dependency structure: the BIS start time (our primary objective) depends on the latest group completion, which in turn depends on breed completion times, which depend on assignment and sequencing decisions.

### 3.2 Judging Duration

Each breed's judging time is deterministic once the judge assignment is known:

- Standard judges evaluate at **25 dogs per hour** (2.4 minutes per dog).
- Permit (provisional) judges evaluate at **20 dogs per hour** (3.0 minutes per dog).
- A judge with a rate override uses the specified rate.

Each breed's judging is divided into two phases:
- **Class judging:** covers all class (non-champion) entries. Duration $\delta_b^{class}$.
- **Specials judging:** covers champion entries plus the BOB competition. Duration $\delta_b^{specials}$.

Class judging must precede specials judging within each breed.

The total judging time for breed $b$ is $\delta_b = \delta_b^{class} + \delta_b^{specials}$.

### 3.3 Segments

A key structural concept is the **segment**: a contiguous block of breeds assigned to a single judge in a single ring. Segments arise naturally from the scheduling problem — a judge typically handles several related breeds in sequence in one ring, then may move to another ring for additional breeds.

Segments are pre-computed in a **preprocessing step** (Phase B) based on:
- AKC rules on maximum dogs per segment (see §4.6)
- Equipment type grouping (table, ramp, floor breeds grouped together to avoid equipment switching within a segment)
- Judge entry limits (ensuring no judge's total exceeds 175 entries)

The MIP then schedules segments — not individual breeds — to rings and time slots, significantly reducing problem size.

### 3.4 Exhibitor Conflicts

*Deferred — see §10.2.*

---

## 4. AKC Rules and Constraints

The following AKC regulations (from the *Rules Applying to Dog Shows*, *Guidelines for Dog Show Judges*, and *Show Manual §6*) govern the scheduling problem as hard or soft constraints.

### 4.1 Judge Entry Limits (Hard)

- A judge may evaluate no more than **175 dogs** in breed judging per day at an all-breed show.
- If a judge is also assigned a group, the recommended ceiling is **150 breed entries** (the group entries are not counted toward the 175 limit, but the time they consume must be accounted for).
- Permit judges are subject to the same numerical limits but judge at a slower rate.

### 4.2 Mandatory Lunch Break (Hard)

- Any judge whose assignment spans more than **5 hours** of active judging time must receive a lunch break of at least **45 minutes** (the show may specify a longer duration).
- The lunch break must fall within the show's designated lunch window (typically 11:30 AM – 1:30 PM).
- The break must occur between segments; it cannot interrupt a breed.

### 4.3 Sequencing (Hard)

- Within each breed: class judging precedes specials judging.
- Group judging for group $g$ cannot begin until all breeds in group $g$ have completed BOB (i.e., completed specials judging).
- BIS cannot begin until all seven groups have completed group judging.
- A judge's segments must be ordered sequentially (no simultaneous judging).

### 4.4 Ring Usage (Hard)

- No two events may occupy the same ring simultaneously.
- Group and BIS judging must occur in rings designated as group rings.
- Group rings may also be used for breed judging earlier in the day; they are not exclusively reserved for group/BIS purposes.
- A group ring must be **free** at the time its group or BIS event is scheduled to begin — no breed judging may continue into or overlap with a group/BIS event in that ring.

### 4.5 Judge Conflict Rules (Hard)

- No judge may be assigned to both a breed and that breed's group at the same show.
- No judge may be assigned to both a group and Best in Show.
- Consequently, no judge can hold all three of: a breed, its group, and BIS.

### 4.6 Segment Size Rules (Hard / Soft — AKC Show Manual §6)

- **Soft cap (time-based):** segments should be approximately one hour of judging. This translates to `⌊60 / rate_mpd⌋` dogs — 25 dogs for standard judges, 20 for permit judges.
- **Hard cap (multi-breed segments):** no multi-breed segment may contain more than **50 dogs**.
- **Single-breed exception:** a breed with more than 30 entries receives its own dedicated segment with no dog-count cap (AKC: *"except in cases where the entry in a breed or variety exceeds 30"*).

### 4.7 Lunch Availability for Non-Mandatory-Break Judges (Soft)

- Judges not requiring a mandatory break should nonetheless have at least one inter-segment gap during the lunch window where they could reasonably take a break.
- This is modeled as a soft penalty rather than a hard constraint.

---

## 5. Mathematical Formulation

### 5.1 Preprocessing

Before the MIP is solved, a deterministic preprocessing step produces:

1. **Durations.** For each breed $b$ with judge $j(b)$:
$$\delta_b^{class} = \lceil n_b^{class} \cdot r_{j(b)} \rceil$$
$$\delta_b^{nr} = \lceil n_b^{nr} \cdot r_{j(b)} \rceil$$
$$\delta_b^{specials} = \lceil (n_b^{specials} + 1) \cdot r_{j(b)} \rceil$$
where $r_j$ is judge $j$'s minutes-per-dog rate and the $+1$ in the specials term accounts for the BOB evaluation. All durations are rounded up to the nearest time-slot multiple.

   The ordering of phases within the breed depends on the `nonregular_position` field of the breed's BreedEntry:
   - `before_specials`: order is **class → nonregular → specials**; total $\delta_b = \delta_b^{class} + \delta_b^{nr} + \delta_b^{specials}$
   - `after_specials`: order is **class → specials → nonregular**; total $\delta_b = \delta_b^{class} + \delta_b^{specials} + \delta_b^{nr}$

   When $n_b^{nr} = 0$, `nonregular_position` is ignored and the two orderings are equivalent.

2. **Segment packing.** Breeds assigned to the same judge are packed into segments using a greedy bin-packing procedure (see §4.6 for size rules). Table breeds are kept consecutive, and ramp breeds are kept consecutive, within each segment to minimize equipment switching. The result is a set of segments $S$, each with a known duration $D_s$, a judge $j(s)$, an ordered list of breeds, and an equipment profile.

3. **Lunch determination.** The set $J^{break} \subseteq J$ of judges requiring a mandatory lunch break is identified: judge $j \in J^{break}$ if $\sum_{s: j(s)=j} D_s > 300$ minutes.

4. **Conflict pairs.** *Deferred — see §10.2.*

### 5.2 Index Sets

| Symbol | Description |
|--------|-------------|
| $S$ | set of segments |
| $J$ | set of judges |
| $J^{break}$ | judges requiring mandatory lunch |
| $J^{soft}$ | judges with ≥ 2 segments not in $J^{break}$ (soft lunch candidates) |
| $R$ | set of rings |
| $R^G \subset R$ | group/BIS rings (the "arena") |
| $G$ | set of AKC groups |
| $SP$ | same-judge segment pairs $\{(s_a, s_b) : j(s_a) = j(s_b),\ s_a < s_b\}$ |
| $XP$ | cross-judge segment pairs $S \times S \setminus SP$ (for ring non-overlap) |
| $LG$ | mandatory-lunch gap pairs $\{(j,i) : j \in J^{break},\ 0 \le i < \lvert S_j \rvert - 1\}$ |
| $SG$ | soft-lunch gap pairs (same structure, $j \in J^{soft}$) |
| $JGS$ | judge-group-segment triples $\{(j,g,s) : j \text{ judges } g,\ s \in S_j\}$ |
| $RS$ | ring-switch pairs $\{(j,i) : \lvert S_j \rvert \ge 2,\ 0 \le i < \lvert S_j \rvert - 1\}$ |
| $j(s)$ | judge assigned to segment $s$ |
| $S_j$ | segments assigned to judge $j$, ordered by preprocessing index $i$ |

### 5.3 Parameters

| Symbol | Description |
|--------|-------------|
| $D_s$ | duration in slots of segment $s$ |
| $D_g^{grp}$ | duration in slots of group $g$ judging |
| $D^{BIS}$ | duration in slots of BIS (fixed, 20 min) |
| $T$ | total slots in judging window (relative, 0-indexed) |
| $T_0$ | absolute slot index of judging start |
| $t_L^{start}, t_L^{end}$ | lunch window start/end (relative slots) |
| $L$ | mandatory break duration in slots |

### 5.4 Decision Variables

**Segment assignment and timing:**

| Variable | Domain | Description |
|----------|--------|-------------|
| $u_{s,r}$ | $\{0,1\}$ | 1 if segment $s$ assigned to ring $r$ |
| $\sigma_s$ | $\mathbb{R}_{\ge 0}$ | start slot of segment $s$ (continuous) |

This is a **disjunctive formulation**: ring assignment $u_{s,r}$ and start time $\sigma_s$ are separate variables, linked by the non-overlap constraints. This replaces the time-indexed $x_{s,r,t}$ formulation in the original design (see §9.1).

**Ordering variables** (all binary):

| Variable | Description |
|----------|-------------|
| $\text{ord}_{s_a, s_b}$ for $(s_a,s_b) \in SP$ | 1 if $s_a$ precedes $s_b$ (same judge) |
| $\text{ord}^\text{rp}_{s_1,s_2}$ for $(s_1,s_2) \in XP$ | 1 if $s_1$ precedes $s_2$ (ring non-overlap) |
| $\text{ord}^\text{arena}_{g_1,g_2}$ for $(g_1,g_2) \in GG$ | 1 if group $g_1$ runs before $g_2$ in arena |
| $\text{ord}^\text{jg}_{j,g,s}$ for $(j,g,s) \in JGS$ | 1 if breed segment $s$ finishes before group $g$ |
| $\text{ord}^\text{bis}_{s}$ for $s \in S_{j^{BIS}}$ | 1 if breed segment $s$ finishes before BIS |

**Group and BIS timing** (continuous):

| Variable | Domain | Description |
|----------|--------|-------------|
| $\tau_g$ | $\mathbb{R}_{\ge 0}$ | start slot of group $g$ judging |
| $\tau^{BIS}$ | $\mathbb{R}_{\ge 0}$ | start slot of BIS |

**Lunch variables:**

| Variable | Domain | Description |
|----------|--------|-------------|
| $\ell_j$ | $[t_L^{start},\ t_L^{end} - L]$ | lunch start slot for judge $j \in J^{break}$ |
| $\lambda_{j,i}$ | $\{0,1\}$ | 1 if gap $i$ is the lunch gap for judge $j \in J^{break}$ |
| $\text{sl\_gap}_{j,i}$ | $\{0,1\}$ | 1 if soft-lunch gap $i$ qualifies for judge $j \in J^{soft}$ |
| $\text{pen}_j$ | $\{0,1\}$ | 1 if no qualifying soft-lunch gap for judge $j \in J^{soft}$ |

**Friction variables:**

| Variable | Domain | Description |
|----------|--------|-------------|
| $z_{j,i}$ | $\{0,1\}$ | 1 if judge $j$ moves to a different ring between segments $i$ and $i+1$ |
| $f_r$ | $\mathbb{R}_{\ge 0}$ | earliest segment start in ring $r$ (symmetry breaking) |

### 5.5 Valid Lower Bounds

Applied as variable lower bounds before solving to tighten the LP relaxation.

**Per-segment start:** segment $i$ of judge $j$ cannot start before all earlier segments of $j$ finish:
$$\sigma_{s_i^j} \ge \sum_{k < i} D_{s_k^j}$$

**Group start:** group $g$ cannot start until all judges whose segments contain a breed in $g$ have finished all their breed segments:
$$\tau_g \ge \max_{j \text{ feeds } g}\ \sum_{s \in S_j} D_s$$

**BIS start:**
$$\tau^{BIS} \ge \max_{j \in J}\ \sum_{s \in S_j} D_s$$

### 5.6 Constraints

#### C1 — Ring assignment

Each segment assigned to exactly one ring:
$$\sum_{r \in R} u_{s,r} = 1 \qquad \forall s \in S$$

#### C2 — Time window

Each segment must finish within the judging day:
$$\sigma_s + D_s \le T \qquad \forall s \in S$$

#### C4 — Ring non-overlap (cross-judge pairs)

For each $(s_1, s_2) \in XP$ and ring $r$, using tight per-segment big-M values:
$$\sigma_{s_1} + D_{s_1} \le \sigma_{s_2} + (T - D_{s_1})(1 - \text{ord}^\text{rp}_{s_1,s_2}) + (T - D_{s_1})(2 - u_{s_1,r} - u_{s_2,r})$$
$$\sigma_{s_2} + D_{s_2} \le \sigma_{s_1} + (T - D_{s_2})\ \text{ord}^\text{rp}_{s_1,s_2} + (T - D_{s_2})(2 - u_{s_1,r} - u_{s_2,r})$$

The $(2 - u_{s_1,r} - u_{s_2,r})$ term deactivates the constraint when the segments are in different rings.

#### C5 — Judge sequencing (same-judge pairs)

For each $(s_a, s_b) \in SP$:
$$\sigma_{s_a} + D_{s_a} \le \sigma_{s_b} + (T - D_{s_a})(1 - \text{ord}_{s_a, s_b})$$
$$\sigma_{s_b} + D_{s_b} \le \sigma_{s_a} + (T - D_{s_b})\ \text{ord}_{s_a, s_b}$$

#### C6 — Arena serialization

All group events share a single logical arena $R^G$. For each $(g_1, g_2) \in GG$:
$$\tau_{g_1} + D_{g_1}^{grp} \le \tau_{g_2} + (T - D_{g_1}^{grp})(1 - \text{ord}^\text{arena}_{g_1,g_2})$$
$$\tau_{g_2} + D_{g_2}^{grp} \le \tau_{g_1} + (T - D_{g_2}^{grp})\ \text{ord}^\text{arena}_{g_1,g_2}$$

#### C7 — Breed segments vacate arena before group/BIS

For each $s \in S$ and $g \in G$:
$$\sigma_s + D_s \le \tau_g + (T - D_s)\Bigl(1 - \textstyle\sum_{r \in R^G} u_{s,r}\Bigr)$$

And for BIS:
$$\sigma_s + D_s \le \tau^{BIS} + (T - D_s)\Bigl(1 - \textstyle\sum_{r \in R^G} u_{s,r}\Bigr)$$

#### C8 — Group waits for all BOBs

For each group $g$ and each segment $s$ containing a breed in $g$ (no big-M — direct precedence):
$$\sigma_s + D_s \le \tau_g$$

#### C9 — BIS waits for all groups

$$\tau_g + D_g^{grp} \le \tau^{BIS} \qquad \forall g \in G$$

#### C10 — End of day

$$\tau_g + D_g^{grp} \le T \qquad \forall g \in G$$
$$\tau^{BIS} + D^{BIS} \le T$$

#### C11 — Group/BIS judges don't overlap their breed segments

For each $(j, g, s) \in JGS$:
$$\sigma_s + D_s \le \tau_g + (T - D_s)(1 - \text{ord}^\text{jg}_{j,g,s})$$
$$\tau_g + D_g^{grp} \le \sigma_s + (T - D_g^{grp})\ \text{ord}^\text{jg}_{j,g,s}$$

For each $s \in S_{j^{BIS}}$ (segments of the BIS judge):
$$\sigma_s + D_s \le \tau^{BIS} + (T - D_s)(1 - \text{ord}^\text{bis}_s)$$
$$\tau^{BIS} + D^{BIS} \le \sigma_s + (T - D^{BIS})\ \text{ord}^\text{bis}_s$$

#### C12 — Mandatory lunch breaks

For each $j \in J^{break}$:

Exactly one gap hosts the lunch:
$$\sum_i \lambda_{j,i} = 1$$

Lunch starts after segment $i$ ends (if gap $i$ chosen):
$$\ell_j \ge \sigma_{s_i^j} + D_{s_i^j} - (T - t_L^{start})(1 - \lambda_{j,i})$$

Segment $i+1$ starts after lunch ends:
$$\sigma_{s_{i+1}^j} \ge \ell_j + L - t_L^{end}(1 - \lambda_{j,i})$$

Lunch window:
$$\ell_j \ge t_L^{start}, \qquad \ell_j + L \le t_L^{end}$$

#### C13 — Soft lunch availability

For each $(j, i) \in SG$, gap $i$ qualifies if segment $i$ ends by $t_L^{end} - L$ and segment $i+1$ starts no earlier than $t_L^{start}$:

$$\sigma_{s_i^j} + D_{s_i^j} \le (t_L^{end} - L) + (T - (t_L^{end} - L))(1 - \text{sl\_gap}_{j,i})$$
$$\sigma_{s_{i+1}^j} \ge t_L^{start} - t_L^{start}(1 - \text{sl\_gap}_{j,i})$$

Penalty fires if no gap qualifies:
$$\text{pen}_j \ge 1 - \sum_i \text{sl\_gap}_{j,i} \qquad \forall j \in J^{soft}$$

#### C15 — Ring-switch indicator

For each $(j, i) \in RS$ and $r \in R$:
$$z_{j,i} \ge u_{s_i^j, r} - u_{s_{i+1}^j, r}$$
$$z_{j,i} \ge u_{s_{i+1}^j, r} - u_{s_i^j, r}$$

#### C16 — Symmetry breaking (ring activation order)

For non-group rings only (group rings are structurally distinct and don't participate). For each non-group ring $r$ and segment $s$:
$$f_r \le \sigma_s + T(1 - u_{s,r})$$

Rings activate in non-decreasing order of first use:
$$f_{r_k} \le f_{r_{k+1}} \qquad \text{for consecutive non-group ring indices } k$$

### 5.7 Big-M Values

All big-M values are the tightest valid value derivable from variable domains. No global $T$ is used where a tighter value exists.

| Constraint | Big-M | Justification |
|------------|-------|---------------|
| C4a | $T - D_{s_1}$ | $\sigma_{s_1} \le T - D_{s_1}$ by C2 |
| C4b | $T - D_{s_2}$ | $\sigma_{s_2} \le T - D_{s_2}$ by C2 |
| C5a | $T - D_{s_a}$ | same |
| C5b | $T - D_{s_b}$ | same |
| C6a\_fwd | $T - D_{g_1}^{grp}$ | $\tau_{g_1} \le T - D_{g_1}^{grp}$ by C10 |
| C6a\_rev | $T - D_{g_2}^{grp}$ | same |
| C7a, C7b | $T$ | $\max(\sigma_s + D_s) = T$, $\min(\tau_g) = 0$; $T - D_s$ is insufficient |
| C11a, C11c | $T - D_s$ | per-segment |
| C11b | $T - D_g^{grp}$ | per-group |
| C11d | $T - D^{BIS}$ | BIS duration fixed |
| C12b | $T - t_L^{start}$ | $\ell_j \ge t_L^{start}$ always |
| C12c | $t_L^{end}$ | $\ell_j + L \le t_L^{end}$ always |
| C13a | $T - (t_L^{end} - L)$ | segment end $\le T$ |
| C13b | $t_L^{start}$ | segment start $\ge 0$ |
| C16a | $T$ | cannot be tightened further |

### 5.8 Objective Function

Two-level weighted-epsilon hierarchy implemented as a single weighted sum:

$$\min \quad w_1 \cdot \tau^{BIS} + \sum_{(j,i) \in RS} z_{j,i} + \sum_{j \in J^{soft}} \text{pen}_j$$

where $w_1 = L3_{max} + 1$ and $L3_{max} = |RS| + |J^{soft}| + 1$.

This ensures any 1-slot improvement in BIS start time dominates any possible improvement in the friction terms, without requiring a multi-objective solver framework.

**Level 1 (primary):** Minimize BIS start time $\tau^{BIS}$.

**Level 2 (secondary):** Minimize operational friction — ring switches $\sum z_{j,i}$ and soft lunch penalties $\sum \text{pen}_j$.

> **Note:** Equipment switching within segments is minimized by the preprocessing segment packer (§5.1, step 2), not by an MIP objective term. The original L3 weight decomposition ($w_3, w_4, w_5$) has been replaced by uniform weight 1 for all friction terms, since the weighted-epsilon construction already ensures L1 dominates L2.

### 5.9 Warm Start

A greedy feasible solution is constructed and injected as a MIP start before solving:

1. **Breed segments** are assigned in judge order. For each judge's segments in sequence, the segment is placed at the earliest available slot across all non-group rings, subject to: judge non-overlap, ring non-overlap, and — for mandatory-break judges — a forced $L$-slot gap when the previous segment ends at or after $t_L^{start}$.

2. **Group events** are placed sequentially in the arena, each after all its BOB segments finish and the arena is free, accounting for the group judge's occupancy.

3. **BIS** is placed after the last group ends, accounting for the BIS judge's occupancy.

4. All binary and continuous variables are set from the greedy placement. Variables not explicitly set are filled to their lower bound if $lb > 0$.

---

## 6. Data Model

### 6.1 Physical Format

The input is an Excel workbook with one worksheet per entity. All FK relationships are enforced at read time by the preprocessing pipeline.

### 6.2 Entity Definitions

**Show** (1 row): Show metadata, timing parameters, and time discretization. Key fields: `judging_start`, `judging_end`, `lunch_window_start`, `lunch_window_end`, `lunch_duration_min`, `time_slot_minutes`.

**Rings**: One row per physical ring. Key field: `is_group_ring` (boolean). Group rings are the designated venue for group and BIS judging, but may also be used for breed judging earlier in the day. The MIP enforces that any breed segments in a group ring complete before that ring's group/BIS event begins.

**RingDistances**: Pairwise distances between rings (symmetric; one row per pair). Stored for post-processing and display. Not currently used in the MIP objective, but available for weighting ring-switch penalties by physical distance.

**Groups**: The seven AKC groups.

**Breeds**: One row per breed or variety entered. Varieties are treated as fully independent breeds. Key fields: `equipment_type` (table/ramp/floor), `judging_rate` (standard/permit — the *recommended* rate for this assignment).

**BreedEntries**: Entry counts by sex and class/specials/nonregular split. One row per breed. Key fields:
- `n_class_dogs`, `n_class_bitches` — regular class entries by sex
- `n_specials_dogs`, `n_specials_bitches` — champion entries by sex
- `n_nonregular` — nonregular class entries (Veteran, Stud Dog, Brood Bitch, etc.), shown as a single undifferentiated block
- `nonregular_position` — enum (`before_specials` / `after_specials`): indicates whether the nonregular block is shown before or after champions, as specified on the day's entry. Ignored when `n_nonregular = 0`.

Durations $\delta_b^{class}$, $\delta_b^{nr}$, and $\delta_b^{specials}$ are computed from these counts in preprocessing.

**Judges**: One row per judge. `is_permit` and `judging_rate_override` determine the actual judging rate used in duration computation.

**BreedJudgeAssignments**: One row per breed. Supports recording substitution metadata (original judge, AKC rule citation) for post-processing display.

**GroupJudgeAssignments**: One row per group.

**BISJudgeAssignment**: One row per show.

**Handlers**: One row per handler. `conflict_opt_in = True` is stored but not currently acted upon (conflict modeling deferred — see §10.2).

**Dogs**: Fully populated — one row per dog entered. Enables consistency validation and conflict pair enumeration (when conflict modeling is activated).

### 6.3 Entity-Relationship Summary

```
Show
 ├── Ring (many) ── RingDistance (pairwise)
 ├── Group (many)
 │    └── Breed (many)
 │         ├── BreedEntry (one-to-one)
 │         └── BreedJudgeAssignment (one-to-one)
 ├── Judge (many)
 │    ├── BreedJudgeAssignment (many)
 │    ├── GroupJudgeAssignment (one)
 │    └── BISJudgeAssignment (zero or one)
 └── Handler (many)
      └── Dog (many) → Breed
```

### 6.4 Validation Rules

The preprocessing pipeline enforces the following before constructing the MIP:

- Referential integrity across all FK relationships
- Every breed has exactly one BreedEntry and one BreedJudgeAssignment
- Every group has exactly one GroupJudgeAssignment
- Exactly one BISJudgeAssignment per show
- No judge appears in both GroupJudgeAssignments and BISJudgeAssignment
- No judge is assigned to a breed, that breed's group, and BIS simultaneously
- No judge's total breed entries exceed 175 (warning + flag)
- No judge with a group assignment has more than 150 breed entries (warning)
- Dog counts consistent with BreedEntries counts
- `nonregular_position` is `before_specials` or `after_specials` for all BreedEntry rows with `n_nonregular > 0`; value is ignored (but not required to be null) when `n_nonregular = 0`
- All timing parameters logically consistent

---

## 7. Output Format

The output follows the structure of an AKC judging program as published by show superintendents.

### 7.1 Structure

The program is organized **by ring** (primary axis), then **by time** within each ring. Each ring block contains:

- Ring number and judge name header
- Optional continuation note if the judge arrives from another ring
- Time blocks (e.g., *9:00 AM*) with breeds listed beneath each
- Each breed line formatted as:
  ```
  [total] [Breed Name]  [class_dogs]-[class_bitches]-([specials_dogs]-[specials_bitches])-[nonregular]
  ```
  Example: `27 Pugs  5-7-(10-4)-1`
- When nonregular entries are present, an annotation indicates their position:
  - `NR Before Specials` if `nonregular_position = before_specials`
  - `NR After Specials` if `nonregular_position = after_specials`
- Lunch break shown inline: `12:00 NOON — Lunch`
- Forward reference if the judge continues in another ring:
  `See Ring 4 at 2:15 PM for balance of assignment`
- Total dogs judged in that ring at the end of each ring block

### 7.2 Derivation from MIP Solution

The output is constructed in a post-processing step (`akc_program.py`):

- Ring assignments from $u_{s,r}$
- Breed start times from segment start time $\sigma_s$ plus within-segment cumulative offsets (breed order fixed by preprocessing)
- Lunch position from $\ell_j$ (mandatory-break judges) or the inter-segment gap (other judges)
- Cross-references generated by identifying judges with multiple ring assignments

> **Note:** Within-segment breed ordering is fixed by preprocessing (breeds sorted by equipment type, then by entry count within each equipment group). The MIP does not reorder breeds within a segment. The original $y_{b,b',s}$ within-segment ordering variables have been eliminated.

---

## 8. Implementation Architecture

### 8.1 Phases

**Phase A — Synthetic Dataset Construction** ✅ *Complete*

A parameterized Python generator (`akc_show_generator.py`) produces realistic show input workbooks. Key parameters:

- `--size {small|medium|large}` — controls breed count and entry density
- `--seed` — controls random generation for reproducibility
- `--rings`, `--group-rings` — physical ring configuration
- `--conflict-opt-in-rate` — fraction of handlers requesting conflict protection
- All timing parameters (judging window, lunch window, slot size)

The generator enforces the 175-entry judge limit algorithmically and validates the AKC triple-conflict rule (breed + group + BIS) at generation time.

**Phase B — Preprocessing Pipeline** ✅ *Complete*

`akc_preprocessing.py` reads and validates the workbook, computes durations, packs segments using equipment-aware bin packing (AKC Show Manual §6 rules), identifies $J^{break}$, and outputs a structured `ShowData` object ready for MIP construction.

**Phase C — MIP Solver** ✅ *Complete (warm start stub — pending implementation)*

`akc_mip2.py` is a clean incremental rebuild of the Pyomo model targeting SCIP via pyscipopt. All constraint families (C1–C16) and the full two-level objective are implemented. Solution extraction uses direct SCIP variable querying with normalisation-based name matching. Incumbent progress is logged via a SCIP event handler, reporting BIS time and friction count for each new best solution. The greedy warm start (`_compute_greedy_warmstart`) is currently a stub returning `[]`; implementing it is the primary remaining task.

**Phase D — Output Renderer** ✅ *Complete*

`akc_program.py` generates a formatted text judging program from a `SolveResult`, matching the AKC judging program layout including ring headers, breed lines, lunch annotations, and ring cross-references.

### 8.2 Technology Stack

| Component | Technology |
|-----------|------------|
| Solver | SCIP (via `pyscipopt`); Pyomo model layer for solver-agnosticism |
| Modeling | Pyomo |
| Data I/O | `openpyxl` (read/write), `pandas` (validation and analysis) |
| Output | Text program generator (`akc_program.py`) |
| Language | Python 3.11+ |

> **Note:** The original design targeted Gurobi. SCIP is the current solver due to licensing availability. The Pyomo model layer makes switching back to Gurobi (or HiGHS/CBC) straightforward.

### 8.3 Problem Size

For the current small synthetic show (704 dogs, 119 breeds, 7 judges, 8 rings, 5-minute slots, ~9-hour window = 108 slots):

| Quantity | Actual |
|----------|--------|
| Segments $\lvert S \rvert$ | 30 |
| Binary variables | ~770 (post-presolve) |
| Continuous variables | ~38 (post-presolve) |
| Constraints | ~6,400 (post-presolve) |

For a typical mid-sized show (192 breeds, ~2,200 dogs, 17 judges, 10 rings):

| Quantity | Estimate |
|----------|----------|
| Segments $\lvert S \rvert$ | ~80–120 |
| Binary variables | ~15,000–30,000 |
| Continuous variables | ~200–400 |
| Constraints | ~50,000–150,000 |

The disjunctive formulation (§9.1) reduces binary variables by roughly two orders of magnitude compared to the original time-indexed design.

---

## 9. Design Decisions and Tradeoffs

### 9.1 Disjunctive vs. Time-Indexed Formulation

**Decision:** The MIP uses a disjunctive formulation with continuous start times $\sigma_s$ and binary ordering variables, rather than the time-indexed $x_{s,r,t}$ formulation in the original design.

**Rationale:** The time-indexed formulation requires $|S| \times |R| \times T$ binary variables — approximately 30 × 8 × 108 = 25,920 for the small show, scaling to ~100,000+ for large shows. The disjunctive formulation replaces the entire $x_{s,r,t}$ tensor with $|S| \times |R|$ assignment variables $u_{s,r}$ plus $O(|S|^2)$ ordering variables, yielding ~770 binaries for the small show. This is a >30× reduction.

**Tradeoff:** The LP relaxation of the disjunctive formulation is weaker than that of the time-indexed formulation if big-M values are loose. This is mitigated by using the tightest provable big-M for every constraint (§5.7) and by the valid lower bounds on $\sigma_s$ and $\tau_g$ (§5.5).

### 9.2 Segment-Based Model

**Decision:** Breeds are pre-packed into segments in preprocessing; the MIP schedules segments, not individual breeds. Within-segment breed ordering is fixed by preprocessing.

**Rationale:** Scheduling individual breeds directly would require large $x_{b,r,t}$ or $x_{b,r}$ + $\sigma_b$ variable sets plus $O(|B|^2)$ within-judge ordering variables. Segments reduce this by collapsing groups of related breeds into single scheduling units. Fixing within-segment order (by equipment type and entry count) is a good heuristic that avoids the need for $y_{b,b',s}$ variables entirely, at the cost of potentially suboptimal within-segment conflict resolution (deferred anyway).

### 9.3 Exhibitor Conflict Modeling Deferred

**Decision:** The Level 2 exhibitor conflict objective has been deferred to a future version.

**Rationale:** The core scheduling problem (ring assignment, sequencing, group/BIS precedence) is already a challenging MIP. Adding conflict variables and constraints significantly increases model size and degrades solve time before the base model is well-tuned. The within-segment breed ordering variables ($y_{b,b',s}$) required for accurate conflict window computation have also been eliminated. A future version can reintroduce conflict modeling once the segment-level scheduling is solid.

### 9.4 Weighted-Epsilon vs. Gurobi Multi-Objective

**Decision:** The lexicographic hierarchy is implemented as a single weighted-sum objective with $w_1 = L3_{max} + 1$, rather than Gurobi's native multi-objective framework.

**Rationale:** The weighted-epsilon construction is solver-agnostic and works identically with SCIP, HiGHS, or any other solver. It is also simpler to reason about and debug. The mathematical guarantee (any 1-slot BIS improvement beats any possible L3 gain) is exact, not approximate.

**Tradeoff:** The construction requires knowing $L3_{max}$ in advance. This is straightforward — it is the number of ring-switch pairs plus the number of soft-lunch judges plus 1.

### 9.5 Varieties as Independent Breeds

**Decision:** Breed varieties (e.g., Dachshund Longhaired, Smooth, Wirehaired) are treated as fully independent breeds with no parent relationship.

**Rationale:** Varieties compete independently in the conformation ring. There is no scheduling relationship between varieties of the same breed. Modeling the parent relationship would add complexity without benefit.

### 9.6 Ring Distances Not in MIP Objective

**Decision:** Pairwise ring distances are stored in the data model but not currently used in the MIP objective.

**Rationale:** Ring distance is a proxy for judge convenience when moving between rings. However, the ring-switching penalty ($z_{j,i}$) already captures the discrete switching event. Adding physical distances would require knowing which ring each segment is assigned to before the ring assignment is solved — a chicken-and-egg problem unless linearized at the cost of additional variables. This is a natural extension for a future version.

---

## 10. Open Items and Deferred Features

### 10.1 Known Open Items

**MIP gap closure.** The small show currently solves to ~10% MIP gap in 600 seconds. The LP relaxation has been substantially tightened (tight per-constraint big-M values, valid lower bounds on all continuous variables), and the dual bound is now climbing steadily. Further improvement paths include: additional valid inequalities (e.g., clique cuts on ring-overlap pairs), tighter group-start lower bounds using actual segment content rather than serial judge load, and symmetry-breaking enhancements.

**Judging rate resolution.** The `judging_rate` field on Breed records the *recommended* rate for that assignment. The *actual* rate used in duration computation comes from the assigned judge's `is_permit` flag and `judging_rate_override`. The preprocessing pipeline resolves this to a single minutes-per-dog value per breed and flags any discrepancy.

### 10.2 Deferred Features

**Exhibitor conflict modeling.** The Level 2 objective (minimize exhibitor scheduling conflicts for opted-in handlers) is deferred. Reactivating it requires: (a) reintroducing within-segment breed ordering variables $y_{b,b',s}$ or fixing breed order and computing approximate conflict windows from segment start times, (b) enumerating conflict pairs, and (c) adding conflict detection constraints and the L2 objective term.

**Split breed assignments.** Some breeds are assigned to multiple judges (e.g., dogs judged by one judge, bitches by another). This complicates segment packing and sequencing significantly. Deferred to a future version.

**Junior Showmanship.** Junior Showmanship classes run on a separate schedule and typically use one ring. Not currently modeled.

**Special attractions.** Obedience, rally, and other companion events that may share ring time or physical space. Not in scope.

**Specialty/supported entry designations.** Some breeds at all-breed shows are also running as a specialty or supported entry, which affects the premium list format but not the scheduling logic. Display-only concern; not modeled.

**Multi-day shows.** The current model is single-day only. Many AKC events are weekend clusters with related scheduling dependencies across days.

**Physical ring distance in MIP.** As noted in §9.6, incorporating ring distances into the switching cost would require linearizing the distance-weighted ring-switch penalty, adding $O(|S|^2 \cdot |R|)$ auxiliary variables.

---

*Report prepared as part of the AKC Show Scheduling Optimization project.*  
*Mathematical specification: `akc-model-spec.md` — keep in sync with `akc_preprocessing.py` and `akc_mip2.py`, and with this report also*.
