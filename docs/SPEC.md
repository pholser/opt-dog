# Judging-program scheduler — model specification (DRAFT)

Status: draft for review. Every constraint cites its source: an AKC document
(`docs/akc/`), a user decision (U), or evidence from parsed real programs (E, to be
filled in once `data/` is parsed). Items marked **OPEN** need a decision.

## 1. Problem

Given a one-day AKC all-breed conformation show's judges, their breed assignments,
the entries, and the available rings, produce a judging program:

- split each judge's assignment into judging **blocks**,
- place every block in a **ring** at a **start time**,
- choose the **order of the seven groups**, which run in the group ring(s) followed by BIS,

so that all hard constraints hold, the show **finishes as early as possible**, and
soft costs (chiefly exhibitor conflicts) are low.

This is a constraint-programming / optimization problem: a scheduling problem with
parallel machines (rings), a resource per judge, precedence (breeds before their group),
and a partitioning sub-problem (forming blocks).

## 2. Inputs

### 2.1 Time
| Symbol | Meaning |
|---|---|
| `T0` | official judging start (e.g. 8:00) — Show Manual §6 says on or before 9:00 |
| `Tend` | posted show closing time; all judging must finish by then (Show Manual §6) |
| `Δ` | time grid for published block times. Real programs use 5-min times (9:25, 10:10) (E) — default 5 min |
| `[L0, L1]` | lunch window: every lunch break must **start** in it (H6). An **input** set ahead of time by the club, usually by when the venue's concessions serve lunch; not a scheduling decision (U). `Show.lunch_window`; when a show gives none (as with all parsed programs), the default `ShowParameters.lunch_window` = 11:00–12:30 (E: published lunches start 11:10–12:30) |
| `m_X` | exhibitor travel margin between rings, in minutes. Parameter, default **2** (U) |
| `m_J` | judge travel margin between rings, in minutes. Parameter, default **2** (U) |

### 2.2 Rings
| Symbol | Meaning |
|---|---|
| `R` | conformation rings available for the day |
| `adj ⊆ R×R` | ring adjacency / distance (used for group ring pairs and judge moves) |
| `RG ⊆ R` | group ring(s): 1 ring, or 2 **adjacent** rings that act as **one** ring for groups/BIS (U). They also host breed blocks before groups. **OPEN**: input, or chosen by the program? |
| `ring size` | **OPEN**: some breeds need a large ring (Show Manual §6: "rings must be large enough for the largest breed"). Needed? |

### 2.3 Judges
| Symbol | Meaning |
|---|---|
| `J` | judges |
| `ρ_j` | minutes per dog: 2.4 (25/hr) by default; 3.0 (20/hr) for permit/sweepstakes judges (Rules Ch.7 §12; Show Manual §6). Per-judge override allowed |
| `fixed_j` | optional fixed commitments (e.g. a specialty pinned at 9:00) (E) |

### 2.4 Judging units
A **unit** is the smallest thing judged without interruption: a breed/variety, a
sweepstakes, Junior Showmanship, Best in Miscellaneous.

| Symbol | Meaning |
|---|---|
| `U` | units |
| `j(u)` | assigned judge (input; reassignment for overload is out of scope) |
| `n_u` | entries, with breakdown dogs–bitches–(BOB dogs–BOB bitches)–nonregular |
| `grp(u)` | AKC classification: one of the 7 variety groups, Miscellaneous Class, or none (FSS breeds, juniors, sweepstakes). Only variety-group breeds feed a group (H7); Miscellaneous breeds compete for Best in Miscellaneous in the breed ring |
| `eq(u)` | equipment: table / ramp / floor (AKC table_breeds list) |
| `d_u` | duration = `n_u · ρ_j(u)`; juniors at 20/hr (Show Manual §6) |
| `fixed_u` | optional pinned start time (specialties, sweepstakes) (E) |
| `pred(u,v)` | optional precedence, e.g. sweepstakes before the breed (E) |

### 2.5 Groups and BIS
| Symbol | Meaning |
|---|---|
| `G` | the 7 groups |
| `judge(g)`, `judge(BIS)` | group and BIS judges |
| `D_g` | group duration = (number of breeds/varieties of group `g` entered in the show) × group judge's rate. Assumes at most one BOB/BOV winner per breed/variety competes in group (U) |
| `D_BIS` | BIS duration = (number of variety groups with ≥1 breed entered, normally 7) × BIS judge's rate (U) |
| | Both use the judge's breed rate (U). Not every breed winner competes in group, so `D_g` and `D_BIS` are conservative upper bounds; on the 11 parsed shows they sum to 5–7½ h, so modeled group/BIS end times run well past real show close and are not a calibration target (U) |

### 2.6 Exhibitors (U)
| Symbol | Meaning |
|---|---|
| `X` | exhibitors: whoever takes the dog into the ring. Each dog entry has one exhibitor. A professional handler is a *team*: if the handler needs help, the assistant who takes the dog in is the exhibitor on that dog, and assistants may register like anyone else (U) |
| `X* ⊆ X` | exhibitors registered for non-conflict accommodation |
| `dogs(x)` | for each exhibitor: the entries they show, each tagged *class dog* or *champion (BOB/specials)* |

Real programs don't include exhibitor data, so test instances use **synthetic**
exhibitor/handler data for now (U). Generating it realistically is its own task:
a few professional handlers show many dogs across breeds, and many owner-handlers show one or two.

### 2.7 Busy windows within a unit (U)
Inside a breed unit, judging runs: regular class dogs → regular class bitches →
non-regular classes → Best of Breed (champions + WD/WB). If `p_u` is the planned
start of unit `u` (block start + durations of the units ahead of it in the block):

- Class-dog window: `[p_u, p_u + d_u)`. An exhibitor showing a **class dog** is
  busy for the **whole breed**, whose length depends on how many dogs are entered (U).
- Champion window: `[p_u + (n_dogs + n_bitches + n_nonreg)·ρ, p_u + d_u)`. An
  exhibitor showing a **champion** is busy only for the **BOB portion** (U).

## 3. Decisions

1. **Block formation**: partition each judge's units into an ordered sequence of
   blocks `b`, with a fixed unit order inside each block. Block duration `D_b = Σ d_u`.
2. **Block placement**: ring `r_b ∈ R` and start `s_b` on the `Δ` grid.
   `s_b` is the published "not before" time.
3. **Judge breaks**: for judges who need lunch, which gap between consecutive blocks is the break.
4. **Groups**: a permutation π of `G`; start times `τ_g`; BIS start `τ_BIS`.

## 4. Hard constraints

| # | Constraint | Source |
|---|---|---|
| H1 | Each block lasts **about one hour** (`D_b ≤ 60 + ε`, **OPEN** ε, measured from data E). Exception: a single unit with `n_u > 30` is its own block, with no cap | Rules Ch.7 §12 |
| H3 | Each ring holds at most one block at a time | — |
| H4 | A judge is never in two rings at once: their blocks, their group/BIS assignment, and their lunch never overlap. When a judge's consecutive blocks are in different rings, the next starts ≥ `m_J` minutes after the previous ends (travel margin) | U |
| H5 | Units in a block are judged in the published order; no block starts before `s_b` | Rules Ch.7 §12; REJ999 |
| H6 | A judge whose assignment exceeds 5 hours gets a break of 45–60 min between two blocks. **Every lunch break a judge is assigned starts inside the lunch window `[L0, L1]`** (hard). A break after the judge's last block is free time, not a break in judging, and is exempt here and in H14 | Rules Ch.7 §12 (break); U (window) |
| H7 | Group `g` starts only after **every block containing a unit of group `g`** has ended | Show Manual §6; U |
| H8 | Group and BIS judges finish all their breed blocks before their group or BIS starts | Show Manual §6 |
| H9 | The first group starts only once **every** group ring in `RG` is **free of breed blocks**, even when two rings are used (U). An optional set-up buffer is a parameter | Show Manual §6; U |
| H10 | Groups run **sequentially** in `RG` (two adjacent rings act as one) in order π; **BIS starts as soon as the last group ends** | U |
| H11 | Pinned units start at their fixed time; precedences hold. Instances built from parsed programs pin every concurrent-specialty, Beginner Puppy and FSS open-show block at its published ring and time (`instance.py`): clubs publish those times, and their events chain (puppy breeds → puppy groups → Best Puppy; FSS show #1 → #2 with the same dogs) | E |
| H12 | Everything ends by `Tend`; nothing starts before `T0` | Show Manual §6 |
| H13 | The breeds of at least **two** groups (parameter, default 2; fewer if fewer groups are entered) are all done before the first group starts | Show Manual §6: "Breed judging in at least two groups should be scheduled to be complete before group judging is scheduled to begin"; U |
| H14 | The first group starts only after **every** judge's lunch has ended: a published break counts as published; a judge over five hours with no published break takes lunch in a gap of lunch length, at the minimum length. Parameter `groups_after_all_lunches`, default on | U; E: holds on all 11 parsed days. Not an AKC rule |
| H15 | Multi-entry classes (Stud Dog, Brood Bitch) come after the judge's regular classes in that breed. Sweepstakes immediately precede their breed (convention, E) | Show Manual §6 "Scheduling Breed Judging"; E |
| V1 | (input check, not a decision) Each judge has ≤ 175 entries, juniors and sweeps included; 200 for a specialty-only judge | Rules Ch.7 §13; Show Manual §6 |

## 5. Soft constraints / costs

| # | Cost | Source |
|---|---|---|
| S1 | **Exhibitor conflict minutes**, counted **by breed** (U): for each registered exhibitor `x ∈ X*`, their dogs in one unit merge into one busy window (any class dog → the whole unit; champions only → just the BOB portion, §2.7). Several dogs in the same unit are never a conflict. For each pair of `x`'s windows in *different units*, the cost is the **minutes of overlap**. If the two units are in **different rings**, one window is first widened by the travel margin `m_X` (so units back to back across rings with less than `m_X` between them still conflict). Units in the same ring never overlap, so they never conflict | U |
| S2 | **Judge ring changes**: penalty for each change between consecutive blocks; a heavier penalty when the change isn't at lunch | Best Practices; Show Manual §6; U |
| S3 | **Equipment switches**: cost per change of equipment (ground / table / ramp) between consecutive breeds **in a ring**, across judges sharing the ring. A switch **across a break** is free: the ring is idle at least `equipment_free_gap_minutes` between the two breeds (default: a lunch's length, 45 min) (U). Within a judge's sequence, table breeds consecutive and ramp breeds consecutive, applied within each group run since S4 takes precedence (§8: humans keep table breeds together within a group 69% of the time, across a whole assignment only 37%) | Show Manual §6 "Scheduling Rings": "Within a judging assignment, all breeds judged on a table should be scheduled consecutively, and all breeds judged on a ramp should be scheduled consecutively"; Best Practices: "schedule table breeds and ramp breeds being judged in the same ring together to decrease the movement of equipment which causes delays" |
| S4 | **Group split**: a judge with breeds in several groups judges each group's breeds in one consecutive run. Cost per extra run of a group in a judge's sequence (soft, U: humans split a group on 37 of 129 judge-days, 83 extra runs) | Show Manual §6 "Scheduling Breed Judging": "If judges are assigned breeds in more than one group, organize their breed-judging schedule by group"; U |
| S5 | Idle gaps in a judge's day (other than lunch) | E/convention |
| S6 | Short-nosed (brachycephalic) breeds in the cooler part of the day (outdoor shows) | Show Manual §6 |
| S7 | Permit judges at non-conflicting times (for the AKC field rep) | Show Manual §6 |
| S8 | **Final segment over 50 dogs**: a ring's final segment may stretch past an hour; each dog over 50 (unless it is a single breed/variety) costs `w_final`. AKC states this as a rule, but it is a soft cost here because real programs exceed it (§8) | Rules Ch.7 §12; U; E |
| S9 | **Short blocks**: each block of fewer than `short_block_dogs` dogs (default 15) costs `w_short` per missing dog. A judge's only block is exempt: it cannot be made larger (U: "absolutely discourage short blocks"). Humans have 64 of 517 blocks under 15 dogs, mostly a judge's whole small assignment or the remainder before lunch or at day's end (§8) | U; E |
| S10 | **Lunch at a logical stopping point**: a cost when a judge's lunch splits a group run (soft, small) | Show Manual §6 "Scheduling Lunch and Breaks": "Try to schedule the lunch or rest break at a logical stopping point" |

## 6. Objective

**Weighted sum** (U):

    minimize  w_F · (τ_BIS + D_BIS − T0)  +  Σ_k w_k · S_k

The weights are configurable per show. Defaults are **OPEN**; we'll calibrate them on
real instances, e.g. by asking "how many minutes of finish time is one exhibitor
conflict worth?".

## 7. Open questions

1. ~~Objective form~~ → weighted sum (U). Default weights still **OPEN**.
2. ~~Conflict definition~~ → busy windows by class vs. champion (§2.7), cost =
   overlap minutes, with a parameterized travel margin `m_X` between rings (U).
3. ~~Exhibitor vs. handler~~ → synthetic exhibitor/handler data for now (U).
   The generation model is still **OPEN**.
4. **Group ring(s)**: are they an input, or does the program choose them?
5. ~~Group / BIS durations~~ → proportional to the number of breeds/varieties entered (group) and groups entered (BIS), at the judge's rate (U).
6. ~~Block tolerance ε and lunch window~~ → ε = 15 min (E); lunch window hard, 11:00–12:30 by default (U, E).
7. **Ring capacity / size classes**: are they needed?
8. **Solver approach**: decide after the data is in. CP-SAT (interval variables,
   NoOverlap, optional intervals) fits placement well. Block formation could be built
   into the same model or done as a separate first stage; we'll compare both on real instances.

## 8. Evidence from real programs (E)

From `docs/calibration.md`: the validator (`src/optdog/validate.py`) run on 11 parsed
all-breed show days, 653–2,137 breed entries each. Timings assume 25 dogs/hr.

- **Rate.** Superintendents schedule at 25 dogs/hr: the median published gap is exactly
  2.40 min/dog. Times are rounded to 5-minute marks, so a block is often 1–2 dogs over the hour.
  → `overlap_tolerance_minutes = 5` (one slot). This leaves 16 overlaps on 11 days,
  down from 248 at zero tolerance.
- **Blocks (H1).** Multi-breed blocks: median 24 dogs, p90 27, max 39. Blocks over 75 min
  that aren't a single breed of more than 30: 7 on 6 of 11 days. → `block_tolerance_minutes = 15` holds.
- **Final segment (H2) is bent.** 12 final segments of 51–73 mixed-breed dogs on 4 of 11
  days; checked against the source, e.g. a 67-dog, 21-breed terrier afternoon. → made a
  soft cost, S8 (U).
- **Lunch (H6).** Every published lunch is 45 min (one 60), starting 11:30–12:20 (p10–p90), 11:10–12:30 overall.
  Every lunch within an assignment fits the default 11:00–12:30 window. One 12:45 "break" comes
  after the judge's last block and is exempt.
  All judges over five hours have one. → `lunch_window = 11:30–12:30`.
- **Group rings (H9) are bent.** The group ring(s) are usually free 83 min before groups (median),
  but on 2 days breed judging continues in the second group ring after groups begin.
  → H9 stays hard: **both** rings must be free before the first group (U), stricter than those 2 days.
- **Group order.** The first group's breeds always finish ≥ 29 min before groups start.
  Working breeds finish 26–119 min *after* the published group start on every show, so
  Working is never first. Humans order groups by readiness, as the model will.
- **Running order (S3, S4).** Of 129 judge-days, 73 judges have breeds in more than one group;
  92 judge-days keep every group in one run (37 split a group, 83 extra runs). Table breeds are
  consecutive across a whole assignment for 34 of 93 judges with table breeds, ramp breeds for
  14 of 26. Within each group run, table/ramp breeds are contiguous 69% of the time. Varieties of
  one breed are consecutive 82% of the time. Humans resolve the S3/S4 tension group-first.
- **Short blocks (S9).** 517 all-breed blocks: p5 9 dogs, p10 13, median 24; 64 under 15 dogs,
  42 under 12. Of those 42: 10 are the judge's only block, 14 the judge's last, 12 mid-day
  (mostly the block just before lunch), 6 the first.
- **Groups** start 1:15–2:30 PM; judging starts 8:00 or 8:30.
- **V1.** One judge at 178 entries (> 175).
- **H13 holds.** On 10 of 11 days the human programs have ≥ 2 groups' breeds done by the
  published group start. The exception is 10 minutes short.
- **Groups start later than the model needs.** Under H1–H13, the greedy baseline can start
  groups at 11:10 AM–2:55 PM (median about noon); humans publish 1:15–2:30 PM. Best Practices
  asks for "the earliest possible opportunity". Whatever else holds humans back is not yet modeled.
  Candidates: exhibitors handling group dogs while breed judging continues, and convention.
- **H14 holds.** On every parsed day the first group starts after the last published lunch ends
  (last lunch ends 12:15–1:15 PM). Made a hard rule (U). With it, the greedy's groups start
  12:40–2:55 PM, still 15–75 min ahead of the humans on most days.

## 9. Validation plan

- Parse real programs → instances (§2 inputs) + human schedules.
- An independent **validator** checks any schedule against H1–H12 and scores S1–S7.
- Run the validator on the human schedules first, to calibrate ε and the lunch window
  and to find which "hard" rules humans actually bend.
- Use lower bounds on finish time (critical path, ring capacity) and a greedy baseline,
  so solver results can be judged. Done: `solve/bounds.py`, `solve/greedy.py`, results in
  `docs/baseline.md`. On the 11 days the greedy schedule satisfies every hard constraint
  except one input-level V1. It finishes a mean 48 min above the bound, against 109 for the human programs.
  With the window hard, the bound uses H14: if any judge needs lunch, no group starts before
  `L0` + the minimum lunch. A judge who needs lunch is free no earlier than `L0` + lunch +
  their shortest event.
- **CP-SAT step 1** (`solve/cpsat.py`): with the greedy's blocks fixed, place them (ring, start,
  lunches, group order) under H1–H14, minimizing finish, then tidy (fewest ring changes, earliest
  blocks) at that finish. All 11 days solve to proven optimal in ≤ 1 s and pass the validator.
  It beats the greedy on 4 days (5–35 min) and matches it on the rest. What holds up the first group:
  H14 (last lunch) on 5 days, H13 on 4, H9 on 2. The remaining gap to the bound is in **block
  formation and order**, i.e. step 3.
- **CP-SAT step 3a** (`solve_formation`): the same model at event level. Each judge's events
  form a chain in the greedy's running order; a cut `c_u` between consecutive events either
  starts a block (on the grid, any ring, travel margin if the ring changes) or continues it
  (`s_u = s_u⁻ + d_u⁻`, same ring). Step 1 is the special case with every cut fixed. H1 caps a
  formed block at 65 min (an event longer than that is its own block); a sweepstakes stays in
  its breed's block (H15). After the finish, a second phase minimizes S2 (ring changes, cheaper
  at lunch), S9 (short blocks), S10 (lunch splitting a group run) and a small per-block
  tie-break; a third starts blocks as early as possible.
  Results: optimal on all 11 days, all valid. Choosing the block breaks gains little:
  4 days improve by 1–7 min; mean gap 42 → 40 min.
  **What holds up groups** (3a re-solved with one rule relaxed at a time, gap in minutes):
  H9 accounts for all 80 on Greater Sierra Vista 11/16; H13 for 44, 20 and 65 on the three
  Palm Springs days; H14 for 14–45 on Del Sur 5/1, Tucson 11/14–15 and GSV 11/17; Sir Francis
  Drake 9/12 (48) is explained by none of the three. H14 binds because of a trade-off the
  bound ignores: a heavily loaded judge who lunches early finishes their breeds later (H7),
  one who lunches late delays the first group (H14). Next: 3b (reorder each judge's group
  runs, which targets H13) and a lower bound that couples lunch with breed completion and
  H9 with ring capacity, to tell solver gap from bound weakness.
  S3 is in the tidy phase: exact within each judge's chain, and between judges at each
  judge's first and last breed (a mid-day ring change is not counted: an approximation;
  the validator counts exactly). Switches inside pinned blocks are constant and left out.
  Equipment switches (validator) on the 11 days: human 33–50, greedy 14–41, 3a 9–32.
  The validator skips H1 for pinned blocks (H11 commitments): a published 84-min Beginner
  Puppy block was flagged only when the solver put another block after it in its ring.
