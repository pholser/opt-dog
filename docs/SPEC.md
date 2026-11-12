# Judging-program scheduler — model specification

Every constraint cites its source: an AKC document (`docs/akc/`), a user decision (U), or
evidence from parsed real programs (E). Items marked **OPEN** need a decision. What is built
and what remains open is summarized in [`SOLUTION.md`](SOLUTION.md).

## 1. Problem

Given a one-day AKC all-breed conformation show's judges, their breed assignments,
the entries, and the available rings, produce a judging program:

- split each judge's assignment into judging **blocks**,
- place every block in a **ring** at a **start time**,
- choose the **order of the seven groups**, which run in the group ring(s) followed by BIS,

so that all hard constraints hold, the show **finishes as early as possible**, and
soft costs are low.

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
| `adj ⊆ R×R` | ring adjacency / distance. **Not implemented**: no ring layouts are available, so neither group ring pairs nor judge moves use it |
| `RG ⊆ R` | group ring(s): 1 ring, or 2 **adjacent** rings that act as **one** ring for groups/BIS (U). They also host breed blocks before groups. An input today (`rings.csv`; `extract` copies the published rings). **OPEN**: should the program choose them? |
| `ring size` | S, M, L or XL per ring (`Ring.size`), a show input; unlisted rings are XL. See H16, H17 |

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
| H1 | Each block lasts **about one hour** (`D_b ≤ 60 + ε`; ε = 15 min in the validator, E; blocks the solver forms are capped at 65 min, `max_block_minutes`). Exception: a single unit with `n_u > 30` is its own block, with no cap | Rules Ch.7 §12 |
| H3 | Each ring holds at most one block at a time | — |
| H4 | A judge is never in two rings at once: their blocks, their group/BIS assignment, and their lunch never overlap. When a judge's consecutive blocks are in different rings, the next starts ≥ `m_J` minutes after the previous ends (travel margin) | U |
| H5 | Units in a block are judged in the published order; no block starts before `s_b` | Rules Ch.7 §12; REJ999 |
| H6 | A judge whose assignment exceeds 5 hours gets a break of 45–60 min between two blocks. **Every lunch break a judge is assigned starts inside the lunch window `[L0, L1]`** (hard). A break after the judge's last block is free time, not a break in judging, and is exempt here and in H14 | Rules Ch.7 §12 (break); U (window) |
| H7 | Group `g` starts only after **every block containing a unit of group `g`** has ended | Show Manual §6; U |
| H8 | Group and BIS judges finish all their breed blocks before their group or BIS starts | Show Manual §6 |
| H9 | The first group starts only once **every** group ring in `RG` is **free of breed blocks**, even when two rings are used (U). An optional set-up buffer is a parameter | Show Manual §6; U |
| H10 | Groups run **sequentially** in `RG` (two adjacent rings act as one) in order π; **BIS starts as soon as the last group ends** | U |
| H11 | Pinned units start at their fixed time; precedences hold. Instances built from parsed programs pin every concurrent-specialty, Beginner Puppy and FSS open-show block at its published ring and time (`instance.py`): clubs publish those times, and their events chain (puppy breeds → puppy groups → Best Puppy; FSS show #1 → #2 with the same dogs). Obedience and rally are out of scope: they run in their own rings (E: no shared ring on the 11 days) and no judge did both on those days. If a judge does both, their obedience and rally blocks are pinned as commitments of that judge, in rings conformation cannot use (U). Exhibitors in both are not modeled | E |
| H12 | Everything ends by `Tend`; nothing starts before `T0`. The solver enforces `T0` only: `Tend` (`closing_time`) is checked by the validator when known, and the problem format has no field for it | Show Manual §6 |
| H13 | The breeds of at least **two** groups (parameter, default 2; fewer if fewer groups are entered) are all done before the first group starts | Show Manual §6: "Breed judging in at least two groups should be scheduled to be complete before group judging is scheduled to begin"; U |
| H14 | The first group starts only after **every** judge's lunch has ended: a published break counts as published; a judge over five hours with no published break takes lunch in a gap of lunch length, at the minimum length. Parameter `groups_after_all_lunches`, default on | U; E: holds on all 11 parsed days. Not an AKC rule |
| H15 | Multi-entry classes (Stud Dog, Brood Bitch) come after the judge's regular classes in that breed. Sweepstakes immediately precede their breed (convention, E) | Show Manual §6 "Scheduling Breed Judging"; E |
| H16 | **Ring size**: each block is in a ring at least as large as its largest breed needs. Ring classes from the Show Manual's suggested sizes (width × unobstructed length, ft): S 24×32 (Toy), M 32×40 (Terrier, Hound, Non-Sporting), L 40×48 (Sporting, Working, Herding; the "larger breeds" of Terrier, Hound and Non-Sporting, 32×50), XL 48×64. Each breed's minimum: Toy S; otherwise table or ramp breeds M, ground breeds L (U: AKC's table/ramp list stands in for size, since the Manual does not say which breeds are "larger"); events without a breed M. The catalog's `min_ring` column overrides the rule per breed. Ring sizes are a show input (`ring_sizes` in show.csv); a ring of unknown size is XL, so without layout data nothing changes | Show Manual §6 "Scheduling Rings": "Assigned rings must be large enough to accommodate the largest breed being judged"; §3 "Breed Rings", ring size table; U |
| H17 | **Group ring size**: every group ring is at least `group_ring_min` (default XL). An input check: group rings are given | Show Manual §3 "Group Rings": "enlarged or combined", "large enough to accommodate the largest number of breeds in any group" |
| V1 | (input check, not a decision) Each judge has ≤ 175 entries, juniors and sweeps included; 200 for a specialty-only judge | Rules Ch.7 §13; Show Manual §6 |

## 5. Soft constraints / costs

| # | Cost | Source |
|---|---|---|
| S1 | **Exhibitor conflict minutes**, counted **by breed** (U), only for exhibitors signed up for the conflict-resolution program; not every exhibitor opts in (U). In the solver it ranks after the structure costs (U), within a judge-idle budget (`conflict_idle_budget`, default 2 × least idle, U). For each registered exhibitor `x ∈ X*`, their dogs in one unit merge into one busy window (any class dog → the whole unit; champions only → just the BOB portion, §2.7). Several dogs in the same unit are never a conflict. For each pair of `x`'s windows in *different units*, the cost is the **minutes of overlap**. If the two units are in **different rings**, one window is first widened by the travel margin `m_X` (so units back to back across rings with less than `m_X` between them still conflict). Units in the same ring never overlap, so they never conflict | U |
| S2 | **Judge ring changes**: penalty for each change between consecutive blocks; a heavier penalty when the change isn't at lunch | Best Practices; Show Manual §6; U |
| S3 | **Equipment switches**: cost per change of equipment (ground / table / ramp) between consecutive breeds **in a ring**, across judges sharing the ring. A switch **across a lunch** is free: a lunch of the judge who leaves or the judge who arrives falls in the ring's idle time between the two breeds, itself at least `equipment_free_gap_minutes` (default 45) (U). A long idle gap with no lunch is not a break: otherwise the solver inserts idle time to make switches free (seen in 3b, SFD 9/12) (U). Within a judge's sequence, table breeds consecutive and ramp breeds consecutive, applied within each group run since S4 takes precedence (§8: humans keep table breeds together within a group 69% of the time, across a whole assignment only 37%) | Show Manual §6 "Scheduling Rings": "Within a judging assignment, all breeds judged on a table should be scheduled consecutively, and all breeds judged on a ramp should be scheduled consecutively"; Best Practices: "schedule table breeds and ramp breeds being judged in the same ring together to decrease the movement of equipment which causes delays" |
| S4 | **Group split**: a judge with breeds in several groups judges each group's breeds in one consecutive run. Cost per extra run of a group in a judge's sequence, scored by the validator; steps 3b and 3c keep each group in one run by construction (soft, U: humans split a group on 37 of 129 judge-days, 83 extra runs) | Show Manual §6 "Scheduling Breed Judging": "If judges are assigned breeds in more than one group, organize their breed-judging schedule by group"; U |
| S5 | Idle gaps in a judge's day, from first to last event. A lunch counts only for its own length (a published break's stated length, otherwise the 45-minute minimum); any wait beyond it is idle. A judge who needs no lunch has none unless the program publishes one, so their long gaps are idle (U). The solver minimizes it after S2, S3, S9 and S10 are fixed; S1 may then raise it up to `conflict_idle_budget` × that minimum, and the last phase minimizes it again with S1 fixed | E/convention |
| S6 | **Not modeled.** Short-nosed (brachycephalic) breeds in the cooler part of the day (outdoor shows) | Show Manual §6 |
| S7 | **Not modeled.** Permit judges at non-conflicting times (for the AKC field rep) | Show Manual §6 |
| S8 | **Final segment over 50 dogs**: a ring's final segment may stretch past an hour; each dog over 50 (unless it is a single breed/variety) costs `w_final`. AKC states this as a rule, but it is a soft cost here because real programs exceed it (§8). Scored by the validator; not in the solver | Rules Ch.7 §12; U; E |
| S9 | **Short blocks**: each block of fewer than `short_block_dogs` dogs (default 15) costs `w_short` per missing dog. A judge's only block is exempt: it cannot be made larger (U: "absolutely discourage short blocks"). Humans have 64 of 517 blocks under 15 dogs, mostly a judge's whole small assignment or the remainder before lunch or at day's end (§8). Avoiding one short block is worth up to `short_block_finish_minutes` of show finish (default 10, U): the solver finds the fastest finish, then minimizes finish + that × short blocks | U; E |
| S10 | **Lunch at a logical stopping point**: a cost when a judge's lunch splits a group run (soft, small) | Show Manual §6 "Scheduling Lunch and Breaks": "Try to schedule the lunch or rest break at a logical stopping point" |

## 6. Objective

The original decision was a **weighted sum** (U), and the validator still reports one as
`ValidationResult.objective`, using `ObjectiveWeights`:

    minimize  w_F · (τ_BIS + D_BIS − T0)  +  Σ_k w_k · S_k

The **solver is lexicographic**. Each phase holds the results of the ones before it:

1. Finish: the earliest BIS end.
2. Trade: finish + `short_block_finish_minutes` × short blocks.
3. Structure: a weighted sum of S2, S3, S9, S10 and block count.
4. Idle: S5 judge idle (with earliest starts as a tie-break). Idle is then capped at
   `conflict_idle_budget` × this minimum (default 2). This phase runs 3 × the phase limit
   (180 s by default): on Palm Springs 1/5 it found least idle ≈ 255 min at 60 s, 168.8 at
   180 s and 167.8 at 300 s.
5. Conflicts: S1, for exhibitors signed up for the conflict-resolution program, within that cap.
   This phase also runs 3 × the phase limit. Its bound stays at 0, so it never proves a minimum;
   two runs each on Greater Sierra Vista 11/17 left 245 and 246 min at 60 s, 81 and 102 at 180 s
   (Tucson 11/14: 227, 251 against 202, 242).
6. Times: S5 judge idle, then earliest block starts.

Phases 4 and 5 run only when some registered exhibitor has two events that can clash.

Only the structure weights affect the solver. **OPEN**: whether to keep the phases or return
to a weighted sum, and what the default weights should be.

## 7. Open questions

1. Objective form: weighted sum decided (U), but the solver is lexicographic (§6). **OPEN**: which stays, and the default weights.
2. ~~Conflict definition~~ → busy windows by class vs. champion (§2.7), cost =
   overlap minutes, with a parameterized travel margin `m_X` between rings (U).
3. ~~Exhibitor vs. handler~~ → synthetic exhibitor/handler data for now (U).
   The generation model is still **OPEN**.
4. **Group ring(s)**: an input today. **OPEN**: should the program choose them?
5. ~~Group / BIS durations~~ → proportional to the number of breeds/varieties entered (group) and groups entered (BIS), at the judge's rate (U).
6. ~~Block tolerance ε and lunch window~~ → ε = 15 min (E); lunch window hard, 11:00–12:30 by default (U, E).
7. ~~Ring capacity / size classes~~ → S, M, L and XL (H16, H17, U). The Show Manual's size rule for the class with the most entries is **OPEN**.
8. ~~Solver approach~~ → CP-SAT, with block formation inside the same model; breeds within a run are packed first (step 3c, §9).

## 8. Evidence from real programs (E)

From `docs/calibration.md`: the validator (`src/optdog/validate.py`) run on 11 parsed
all-breed show days, 653–2,137 breed entries each. Timings assume 25 dogs/hr.

- **Rate.** Superintendents schedule at 25 dogs/hr: the median published gap is exactly
  2.40 min/dog. Times are rounded to 5-minute marks, so a block is often 1–2 dogs over the hour.
  → `overlap_tolerance_minutes = 5` (one slot). This leaves 16 overlaps on 11 days,
  down from 248 at zero tolerance.
- **Blocks (H1).** Multi-breed blocks: median 24 dogs, p90 27, max 39. Blocks over 75 min
  that aren't a single breed of more than 30: 7 on 6 of 11 days. → `block_tolerance_minutes = 15` holds.
- **Final segment (S8) is bent.** 12 final segments of 51–73 mixed-breed dogs on 4 of 11
  days; checked against the source, e.g. a 67-dog, 21-breed terrier afternoon. → made a
  soft cost, S8 (U).
- **Lunch (H6).** Every published lunch is 45 min (one 60), starting 11:30–12:20 (p10–p90), 11:10–12:30 overall.
  Every lunch within an assignment fits the default 11:00–12:30 window. One 12:45 "break" comes
  after the judge's last block and is exempt.
  All judges over five hours have one. → default `lunch_window` = 11:00–12:30 (hard, H6).
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

## 9. Validation plan and results

Entries below are in the order the work was done, and each gives the figures at that time.
Those before the "Group judges" entry predate the parser fix, and judge idle figures before the
"Exhibitor conflicts" entry exclude a whole lunch gap. Current per-day results are in
`baseline.md` and `figures.html`.

- Parse real programs → instances (§2 inputs) + human schedules.
- An independent **validator** checks any schedule against the hard constraints and scores S1–S5 and S8.
  H5, H10, H11 and H15 are not checked independently: they hold by construction in the solver.
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
  one who lunches late delays the first group (H14). Then: 3b (reorder each judge's group
  runs, which targets H13) and a lower bound that couples lunch with breed completion and
  H9 with ring capacity, to tell solver gap from bound weakness.
  S3 is in the tidy phase: exact within each judge's chain, and between judges at each
  judge's first and last breed (a mid-day ring change is not counted: an approximation;
  the validator counts exactly). Switches inside pinned blocks are constant and left out.
  Equipment switches (validator) on the 11 days: human 33–50, greedy 14–41, 3a 9–32.
  The validator skips H1 for pinned blocks (H11 commitments): a published 84-min Beginner
  Puppy block was flagged only when the solver put another block after it in its ring.
- **CP-SAT step 3b** (`solve_ordering`): each judge's events split into segments, one per
  (group, equipment) in the greedy's order; the solver orders the segments (a circuit per judge,
  each group entered once, so a judge's group stays contiguous, S4) and so chooses the judge's
  group order and, within a group, whether e.g. table breeds come before or after ground breeds
  (table and ramp breeds stay consecutive within a group, S3). Cuts, H1 and the soft costs work
  as in 3a across the arcs between segments. S3 also counts the switch where one segment meets
  the next. The published group start is now *equal* to the earliest group start (before, it
  was only a lower bound on every group, and the earliest-blocks phase could publish it before
  any group was ready, which the validator flagged as H7).
  Results (`docs/baseline.md`): all 11 days valid, 10 proven optimal; mean gap to the bound
  41 → 24 min; better than 3a on 8 days, e.g. Palm Springs 1/5 by 65 min, Palm Springs 1/3
  by 38, Sir Francis Drake 9/12 by 34; Greater Sierra Vista 11/17 reaches the bound. Running
  order was the main lever on the days H13 held up. Runtime about 1 min per day.
- **Judge idle (S5)** is in the third phase, ahead of early starts, at the finish and structure costs
  of phase 2. Tested on 3b over the 11 days. Idle minutes counted from first to last event, less
  judging and lunch: 2072 → 1188. Equipment switches 222 → 220, short blocks 82 → 82, finishes
  unchanged. Putting idle in phase 2 instead at its 0.5/min weight gave 1078 idle minutes, but
  raised short blocks to 96 and switches to 244, and phase 2 no longer proved optimal on any day.
- **Short blocks traded for finish (S9).** A phase after the fastest finish minimizes
  finish + `short_block_finish_minutes` (default 10, U) × short blocks; later phases hold both.
  On 3b: short blocks 64 → 52 over the 11 days for 6 min of finish in total. Raising the price
  did not help: on Palm Springs 1/3 even an unlimited price left 10 avoidable short blocks
  (published: 6), because a small breed sits beside one too long to share a block and 3b cannot
  move breeds inside a run.
- **CP-SAT step 3c** (`solve_packing`): as 3b, with the breeds inside each (group, equipment)
  run ordered first, by a small bin-packing solve per run (`solve/packing.py`): fewest short
  bins under the block cap, then fewest bins. A breed stays with its sweepstakes (H15) and the
  varieties of one breed stay together (§8: 82% in published programs). Giving each breed its
  own node in the circuit would cost 2,000–4,800 arcs per day, each with a constraint per ring,
  so the order is fixed before the model runs, and 3c is optimal only for that order.
  Results: short blocks 64 published → 50 (3b) → 22 (3c), 11 of them the whole assignment of a
  judge with under 15 dogs; fewer than published on every day, all valid. Finish 26 min earlier
  in total than 3b (earlier on 5 days, later on 2: Tucson 11/14 +3.6, Palm Springs 1/3 +2.2).
  Equipment switches 229 → 221, judge idle 1205 → 1138. Proven optimal on 9 of 11 days, for the
  packed order. Also fixed: S9 was not counted for a judge whose cuts are all forced.
- **Group judges in the parsed data.** The Bradshaw parser read "Mrs. X" as title "Mr" and
  surname "s. X", so 20 of 88 group and Best in Show assignments loaded as separate judges with
  no breeds, and H8 and the wait for the BIS judge did not apply to them. Results before that
  fix understate these constraints. Fixing it raised the lower bound by up to 66 min (Palm
  Springs 1/5), with published finishes unchanged.
- **Planning without a published program** (`python -m optdog.plan`, `io/problem_csv.py`). A
  problem directory holds only inputs: `show.csv`, `rings.csv`, `judges.csv`, `events.csv`
  (entries, plus an optional pinned ring and start) and `groups.csv`. `check` reports input
  errors and warnings, `solve` runs 3c and writes a program and optional HTML, and `extract`
  strips a published program down to a problem. Del Sur 5/1 extracted and planned this way
  reaches the same finish as the saved 3c (5:00 PM, proven optimal, equal to the bound).
- **Exhibitor conflicts (S1)** are a solver phase after structure and before judge idle, for
  exhibitors signed up for the conflict-resolution program. The variables are added only when
  that phase runs. One pair of windows per pair of events sharing a registered exhibitor (a
  judge's own events and pairs of pinned events are left out): 160–826 pairs per day. On a
  three-day trial of 3c (Del Sur 5/1, Palm Springs 1/4, Sir Francis Drake 9/12), conflicts fell
  56–99% at the same finish and structure cost. Judge idle rose on two of the days, 45 → 361 and
  131 → 1243 minutes; a 300-second idle phase brought those to 225 and 717. A lunch now counts
  as idle only beyond its own length, in the validator as in the solver.
  Idle first, then a budget: a phase minimizes idle, idle is capped at `conflict_idle_budget` ×
  that minimum, and conflicts are minimized within the cap. Trial at 60 s per phase (finish,
  structure and hard rules unchanged): Del Sur 5/1 conflicts 138 → 36 at ×1.25 (idle 40) and
  → 9 at ×2 (idle 88); Palm Springs 1/4 3,524 → 3,112 (idle 151) and → 2,695 (idle 238).
  Conflicts first had reached 7 and 1,606, with idle 225 and 717 even at 300 s. Default ×2 (U).
  The budget is a multiple, so a day whose least idle is 0 gets no idle for conflicts.
- **Relaxation bound** (`solve/relaxation.py`): CP-SAT on a relaxation that keeps judges
  (one event at a time, lunch in the window with judging on both sides), rings as a pool of
  |R| (|R| − |RG| from the first group on, H9), pinned blocks, and H7, H8, H10, H13, H14 exactly;
  it drops blocks (H1, H5), the grid for blocks and lunches, travel, ring identity, running
  order and equipment. Durations rounded down. Its proven optimum is a lower bound on every
  valid schedule; the combinatorial bound is added as a cut and the larger is reported.
  It couples what the combinatorial bound treats separately: a judge's lunch with when their
  breeds finish, and H9 with ring capacity. Proven optimal on all 11 days in seconds (up to
  ~50 s on SFD 9/13). It raises the bound by 0–80 min (GSV 11/16: 1079 → 1159.4, which 3b
  meets, so 3b is optimal there and the earlier "80 min gap" was all bound weakness).
  Against it, 3b's best runs are within 0–10 min on every day (mean 7), and 3c's mean is 4.
