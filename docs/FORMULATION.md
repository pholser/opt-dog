# The optimization problem

This document states the scheduling problem precisely, in English and in mathematics, and
explains why it is modeled this way. It sits between the other two documents:

- [`SPEC.md`](SPEC.md) lists every rule with its source (AKC document, user decision **U**, or
  evidence from published programs **E**). The rule codes H1–H17, V1 and S1–S10 used below
  are SPEC's.
- [`SOLUTION.md`](SOLUTION.md) says what is built and how well it does.

Sections 1–6 define the **reference problem**: the problem that is meant to be solved. Section 7
describes the **restriction** the solver actually solves, and section 8 how far from optimal that
can be. Section 9 collects the modeling choices and why they were made, and section 10 what is
left out.

## 1. The problem in words

A one-day AKC all-breed conformation show has judges, each assigned some breeds; dogs entered
in those breeds; and rings. The **judging program** says, for each judge, how their breeds are
grouped into **blocks** of about an hour, in what order, in which **ring** each block is judged,
and at what published **"not before" time** it starts; when each judge takes **lunch**; and in what
order the seven **variety groups** are judged, in the group ring, before **Best in Show** (BIS).

A program must obey the AKC's rules and the show's physical limits: a ring holds one block at a
time, a judge is in one place at a time, judges over five hours get lunch inside the lunch window,
a group can only start once all its breeds are judged, and so on. Among valid programs we want the
show to **finish as early as possible** (BIS ends earliest). After that, we want few judge ring
changes, few equipment changes, few short blocks, little judge idle time, and few conflicts for
exhibitors who have asked for them to be avoided.

It is a scheduling problem with parallel machines (rings), one resource per judge, precedence
(breeds before their group, groups before BIS), and a partitioning sub-problem (cutting each
judge's breeds into blocks).

## 2. Sets and data

### Sets

| Symbol | Meaning |
|---|---|
| $J$ | judges |
| $U$ | events: one per breed or variety (the smallest thing judged without interruption) |
| $U_j \subseteq U$ | the events assigned to judge $j$ (an input; the program never reassigns them) |
| $R$ | rings |
| $R_G \subseteq R$ | the group ring(s): one ring, or two adjacent rings acting as one (an input) |
| $G$ | the seven variety groups; $G^+ \subseteq G$ those with at least one breed entered |
| $F_g \subseteq U$ | events whose breed belongs to variety group $g$: they "feed" $g$. Miscellaneous and FSS breeds feed no group |
| $P$ | pinned blocks: fixed ring, start and events (from the problem's `pin_ring`, `pin_start`) |
| $X^{\ast}$ | exhibitors registered for conflict resolution |

### Data per event, judge and group

| Symbol | Meaning |
|---|---|
| $n_u$ | entries (dogs) in event $u$, counted from the dogs entered |
| $\rho_j$ | judge $j$'s time per dog: 2.4 min (25/hr), 3.0 min (20/hr) for a permit judge, or the judge's own figure |
| $d_u = n_u \, \rho_{j(u)}$ | planned duration of event $u$ |
| $\mathrm{eq}(u)$ | equipment: ground, table or ramp |
| $\mathrm{need}(u)$, $\mathrm{size}(r)$ | smallest ring size the breed needs; ring size (S < M < L < XL) |
| $W_j = \sum_{u \in U_j} d_u$ | judge $j$'s planned judging time |
| $J^L = \{ j : W_j > 300 \text{ min} \}$ | judges who must get a lunch break |
| $D_g = \rho_{\mathrm{judge}(g)} \cdot (\text{breeds and varieties of } g \text{ entered})$ | group duration |
| $D_{BIS} = \rho_{\mathrm{judge}(BIS)} \cdot \lvert G^+ \rvert$ | BIS duration |

### Parameters (defaults in brackets)

| Symbol | Meaning |
|---|---|
| $T_0$ | when breed judging may begin (required input) |
| $T_{\text{end}}$ | the posted closing time (optional input) [none] |
| $\Delta$ | grid for published times [5 min] |
| $\kappa$ | cap on a formed block's length [65 min in the solver; the validator allows 60 + 15] |
| $[L_0, L_1]$ | lunch window: a lunch must start in it [11:00–12:30, or the show's own] |
| $\lambda$ | lunch length [45 min; up to 60 allowed] |
| $m_J$, $m_X$ | judge and exhibitor travel margins between rings [2 min each] |
| $\sigma$ | set-up time between the last breed in a group ring and the first group [0] |
| $k$ | groups whose breeds must be done before groups begin [2] |
| $N_{\text{short}}$ | a block with fewer dogs is short [15] |
| $\theta$ | show finish worth giving up to avoid one short block [10 min] |
| $\beta$ | judge idle budget for conflict resolution, as a multiple of least idle [2] |

## 3. Decisions

| Variable | Meaning |
|---|---|
| $\pi_j$ | judge $j$'s **running order**: a sequence of $U_j$. Write $u^-$ for the event just before $u$ in $\pi_j$ |
| $c_u \in \{0,1\}$ | **cut**: 1 if $u$ starts a new block. The first event of every judge has $c_u = 1$ |
| $y_{ur} \in \{0,1\}$ | event $u$ is judged in ring $r$; $\sum_r y_{ur} = 1$. Write $\mathrm{ring}(u)$ |
| $s_u$ | start of event $u$; its end is $e_u = s_u + d_u$ |
| $\ell_j$, $j \in J^L$ | start of judge $j$'s lunch |
| $\tau_g$, $g \in G^+$ | start of group $g$ |
| $F$ | the published group start: when the first group begins |
| $b$ | start of BIS |

A **block** is a maximal run of a judge's consecutive events whose first event has $c_u = 1$ and
whose others have $c_u = 0$. Its published time is the start of its first event. The **show
finish** is

$$\Phi = b + D_{BIS}.$$

## 4. Hard constraints

Unless noted, each constraint holds for every event $u$ that has a predecessor $u^-$ in its
judge's running order. "Indicator" $[\cdot]$ is 1 when the condition holds.

**Start and grid (H12, H5).** Nothing starts before judging begins, and a block starts on the
published grid:

$$s_u \ge T_0, \qquad c_u = 1 \;\Rightarrow\; s_u \in \Delta \mathbb{Z}.$$

Here $\Delta\mathbb{Z}$ means a multiple of $\Delta$ on the clock (8:05, 8:10, ...), whatever
$T_0$ is.

**Closing time (H12).** When the show posts a closing time, Best in Show ends by it:

$$\Phi \le T_{\text{end}}.$$

*Why:* Show Manual §6: "Clubs must conclude judging by the posted closing time. For outdoor
events, ensure that judging will be completed before sunset." An outdoor show can give sunset as
its closing time. The limit holds in every phase, so phase 2 cannot trade finish past it. A
closing time earlier than the combinatorial lower bound (section 8) is reported by `plan check`
before solving; one between that bound and the true earliest finish makes phase 1 infeasible,
and `plan solve` says so.

*Why:* published times are "not before" times on 5-minute marks (E: 9:25, 10:10). Events inside
a block have no published time of their own.

**Blocks are judged without a gap, in one ring (H5).**

$$c_u = 0 \;\Rightarrow\; s_u = e_{u^-} \;\wedge\; \mathrm{ring}(u) = \mathrm{ring}(u^-).$$

*Why:* a block is published as one time and one ring. The breeds in it follow one another.

**A judge moves between rings with a margin (H4).**

$$s_u \ge e_{u^-} + m_J \cdot [\mathrm{ring}(u) \ne \mathrm{ring}(u^-)].$$

*Why:* walking to another ring takes time. A margin of 2 minutes is a user decision, adjustable.

**Blocks of about an hour (H1).** If $u$ is in a block that begins at $s_{\text{first}}$, then

$$e_u - s_{\text{first}} \le \kappa,$$

except that an event longer than $\kappa$ is a block on its own, with no cap.

*Why:* AKC Rules Ch.7 §12 asks for "periods of about one hour". Published multi-breed blocks
have a median of 24 dogs and a 90th percentile of 27 (E); at 2.4 min/dog, 27 dogs is 65 min,
hence $\kappa = 65$. The validator, which judges any program including published ones, allows
75 minutes and exempts a single breed with more than 30 entries, as the rule does. The solver's
cap is stricter, so its programs pass the validator with room to spare.

**One block at a time in a ring (H3).** For every ring $r$, the intervals $[s_u, e_u)$ of the
events with $y_{ur} = 1$, together with the pinned blocks in $r$, are pairwise disjoint.

**A judge is in one place at a time (H4).** For every judge $j$, the intervals of their events,
their lunch $[\ell_j, \ell_j + \lambda)$, and their pinned blocks widened by $m_J$ on each side
are pairwise disjoint. A judge's group or BIS assignment is covered by H8 below.

**The ring is big enough (H16).**

$$y_{ur} = 0 \quad\text{whenever}\quad \mathrm{size}(r) < \mathrm{need}(u).$$

*Why:* Show Manual §6: "Assigned rings must be large enough to accommodate the largest breed
being judged." Breed needs come from the Manual's suggested ring sizes (Toy S; table or ramp
breeds M; ground breeds L, U). A ring of unknown size counts as XL, so without layout data this
constraint changes nothing.

**Lunch (H6).** Every judge $j \in J^L$ takes one lunch that starts in the window, on the grid,
and falls *within* their judging, with judging before and after it:

$$L_0 \le \ell_j \le L_1, \quad \ell_j \in \Delta\mathbb{Z}, \quad
\exists\, u, v \in U_j:\; e_u \le \ell_j,\; s_v \ge \ell_j + \lambda.$$

(A pinned commitment can stand in for the judging before or after.) Judges with $W_j \le 300$
min need no lunch.

*Why:* AKC Rules Ch.7 §12 requires a break for an assignment over five hours. The window is a
show input, set by when the concessions serve lunch (U); the default 11:00–12:30 covers every
published lunch (E: 11:10–12:30). A "break" after a judge's last block is not a break in their
judging, so it doesn't count. The model gives every lunch the minimum 45 minutes; a longer gap
counts as judge idle time (S5), which the objective discourages anyway.

**Breeds before their group (H7).**

$$\tau_g \ge e_u \qquad \forall g \in G^+,\; u \in F_g.$$

**Group and BIS judges are free (H8).** Let $u^{\ast}$ be the last event of the group judge of $g$ (or
of the BIS judge). Then

$$\tau_g \ge e_{u^{\ast}} + m_J \cdot [\mathrm{ring}(u^{\ast}) \notin R_G], \qquad
b \ge e_{u^{\ast}_{BIS}} + m_J \cdot [\mathrm{ring}(u^{\ast}_{BIS}) \notin R_G].$$

Pinned blocks of these judges count the same way.

**Groups one after another, then BIS (H10).** The intervals $[\tau_g, \tau_g + D_g)$ are
pairwise disjoint, and

$$F = \min_{g \in G^+} \tau_g, \quad F \in \Delta\mathbb{Z}, \qquad
b \ge \tau_g + D_g \;\; \forall g, \qquad b \ge F.$$

Since $\Phi$ is minimized, BIS starts as soon as the last group ends.

*Why:* two adjacent group rings act as one for groups (U), so groups never run in parallel. The
published group start is the first group's start, so it is on the grid. The others follow
without published times.

**Group rings are clear (H9).**

$$y_{ur} = 1,\; r \in R_G \;\Rightarrow\; e_u + \sigma \le F.$$

*Why:* groups need the ring. When two rings are used, **both** must be clear before the first
group (U). That is stricter than 2 of the 14 published days, where breed judging continued in
the second group ring after groups began (E).

**Two groups' breeds done first (H13).** With binary $z_g$ for "all of $g$'s breeds are done":

$$z_g = 1 \;\Rightarrow\; e_u \le F \;\; \forall u \in F_g, \qquad
\sum_{g \in G^+} z_g \ge \min(k, \lvert G^+ \rvert).$$

*Why:* Show Manual §6: "Breed judging in at least two groups should be scheduled to be complete
before group judging is scheduled to begin." Which two is the solver's choice.

**Groups after every lunch (H14).**

$$F \ge \ell_j + \lambda \qquad \forall j \in J^L.$$

*Why:* this is not an AKC rule. It holds on all 14 published days (E), and the user made it hard
(U). It can be switched off (`groups_after_all_lunches`).

**Pinned blocks (H11).** A pinned block keeps its ring, start and events.

**Input checks (H17, V1).** These involve no decisions, so they are checked on the inputs: every
group ring is at least XL (H17), and no judge has more than 175 entries (V1, AKC Rules Ch.7
§13). V1 is a warning; H17 an error.

## 5. Costs

All costs below are functions of the decisions. Each is a sum over judges, rings or exhibitors.

**Ring changes (S2).** For consecutive events $u^-, u$ of a judge, let
$\chi_u = [\mathrm{ring}(u) \ne \mathrm{ring}(u^-)]$, and let $\mathrm{lunch}_u = 1$ if the
judge's lunch falls between them. Then

$$S_2^{\text{all}} = \sum_u \chi_u, \qquad S_2^{\text{off}} = \sum_u \chi_u \,(1 - \mathrm{lunch}_u).$$

*Why:* Best Practices and the Show Manual discourage moving judges. A move at lunch costs less
(2 against 10), since the judge leaves the ring anyway.

**Equipment switches (S3).** For two breeds judged one after the other in the same ring (by the
same judge or not) with different equipment, a switch costs 1, unless a lunch of the judge who
leaves or of the judge who arrives falls in the ring's idle time between them and that idle time
is at least 45 minutes.

*Why:* Show Manual §6 asks that table breeds and ramp breeds be scheduled consecutively within an
assignment; Best Practices asks the same within a ring, "to decrease the movement of equipment".
A switch over lunch is free because the equipment can be moved while the ring is idle (U). A long
idle gap without a lunch is not free: otherwise the solver inserts idle time to make switches
free (U, after seeing it do so).

**Short blocks (S9).** For a block $\mathcal{B}$ with $N_{\mathcal{B}} = \sum_{u \in \mathcal{B}} n_u$ dogs,

$$\mathrm{short}(\mathcal{B}) = \max(0,\; N_{\text{short}} - N_{\mathcal{B}}), \qquad
S_9 = \sum_{\mathcal{B}} \mathrm{short}(\mathcal{B}), \qquad
S_9^{\#} = \bigl\lvert \{ \mathcal{B} : \mathrm{short}(\mathcal{B}) > 0 \} \bigr\rvert,$$

over the blocks of judges with at least $N_{\text{short}}$ dogs in all: a judge with fewer cannot
avoid a short block.

*Why:* the user wants short blocks "absolutely discouraged" (U). Published programs have 64 of
517 blocks under 15 dogs (E), so 15 marks the bottom tenth of human practice.

**Lunch splitting a group (S10).** $S_{10}$ counts consecutive events $u^-, u$ of the same group
with the judge's lunch between them.

*Why:* Show Manual §6: "Try to schedule the lunch or rest break at a logical stopping point."

**Block count.** The number of blocks, $\sum_u c_u$: a tie-break toward fewer, fuller blocks.

**Judge idle (S5).** For each judge, the length of their day less their judging and lunch:

$$S_5 = \sum_j \Bigl( \max_{u \in U_j} e_u - \min_{u \in U_j} s_u - W_j - \lambda\,[j \in J^L] \Bigr)$$

(pinned blocks included in the max, min and judging time).

*Why:* judges are paid for and wait through idle time. A lunch counts only for its own length;
waiting beyond it is idle.

**Exhibitor conflicts (S1).** Only for registered exhibitors $x \in X^{\ast}$. Within event $u$, an
exhibitor is busy for the whole event if they show a class dog, and only for Best of Breed if they
show only champions:

$$\text{busy}_x(u) = [\, s_u + o^{\text{lo}}_{xu},\; s_u + o^{\text{hi}}_{xu} \,), \qquad
o^{\text{lo}}_{xu} = \begin{cases} 0 & \text{a class dog} \\ \rho_{j(u)} \cdot (\text{dogs judged before BOB}) & \text{champions only} \end{cases}, \quad
o^{\text{hi}}_{xu} = d_u.$$

If $x$ shows several dogs in $u$, their windows merge into one, from the earliest start to the
latest end. For each pair of events $u \ne v$ in which $x$ shows, the cost is the overlap of the two windows,
with one widened by the travel margin when the rings differ:

$$\omega_x(u,v) = \max\Bigl(0,\; \min(\mathrm{hi}_u + \mu,\, \mathrm{hi}_v) - \max(\mathrm{lo}_u - \mu,\, \mathrm{lo}_v)\Bigr), \qquad
\mu = m_X \cdot [\mathrm{ring}(u) \ne \mathrm{ring}(v)],$$

$$S_1 = \sum_{x \in X^{\ast}} \; \sum_{\{u, v\}} \omega_x(u, v) \quad \text{(minutes)}.$$

*Why:* the user defined a conflict this way (U): conflicts are counted by breed (several dogs of
one exhibitor in one breed are never a conflict); a champion's exhibitor is needed only for Best
of Breed; and the cost is minutes of overlap, so a near miss costs little and a full clash a lot.
Exhibitors who have not opted in are not counted (U). Each member of a professional handler's
team counts as their own exhibitor (U).

**Not in the solver.** S4 (a judge's breeds of one group in one consecutive run) is enforced as
a hard rule by the solver's restriction (section 7), and only scored by the validator. S8 (a
ring's final segment over 50 dogs) is scored by the validator only.

## 6. Objective

The solver minimizes **lexicographically**: each phase optimizes one objective while holding what
the earlier phases achieved.

1. **Finish:** minimize $\Phi$. Call the result $\Phi_1$ and its short-block count $S_9^{\#}(1)$.
2. **Trade finish for short blocks:** minimize $\Phi + \theta \, S_9^{\#}$, subject to
   $\Phi \le \Phi_1 + \theta\, S_9^{\#}(1)$. Then fix $\Phi$ and $S_9^{\#}$ at no more than this phase's values.
3. **Structure:** minimize
   $$2\, S_2^{\text{all}} + 8\, S_2^{\text{off}} + 2\, S_9 + 2\, S_{10} + 5\, S_3 + 0.5 \cdot \text{blocks},$$
   then fix it. (A ring change costs 10 off lunch, 2 at lunch.)
4. **Least idle** (only when some registered exhibitor has two events that can clash): minimize
   $S_5$, with a small tie-break toward early starts. Call the result $S_5^{\ast}$ and require
   $S_5 \le \beta\, S_5^{\ast}$ from here on.
5. **Conflicts** (same condition): minimize $S_1$, then fix it.
6. **Times:** minimize $10\, S_5 + \sum s_u + F$, the sum over every event that may start a
   block: least idle, then everything as early as possible.

*Why lexicographic.* Finishing early is the primary goal: Best Practices asks for groups at "the
earliest possible opportunity", and the user chose show finish as the objective (U). A weighted sum
would need an exchange rate between minutes of finish and every soft cost, and no such rates are
known. The phases need only the rates the user has actually given: 10 minutes of finish per
avoided short block (U), and judge idle allowed to double to reduce conflicts (U). Conflicts rank
after the structure costs (U), and judge idle is protected by the budget because minimizing
conflicts alone raised judge idle up to ninefold on trial days (SPEC §9). Solving phase by phase
also helps the solver: each phase starts from the previous phase's solution.

Phase 2 is skipped when $\theta = 0$ or when no block can be short. Every phase runs under a time
limit (60 s by default; 180 s for phases 4 and 5), so a phase's result is the best found, proven
optimal only when the solver says so.

The originally decided form was a weighted sum (U), and the validator still reports one
(`ValidationResult.objective`):

$$1 \cdot (\Phi - T_0) + 1 \cdot S_1 + 10\,S_2^{\text{off}} + 2\,(S_2^{\text{all}} - S_2^{\text{off}}) + 5\,S_3 + 2\,S_4 + 0.5\,S_5 + 2\,S_8 + 2\,S_9 + 2\,S_{10} + 0.5\,\lvert\mathcal{B}\rvert,$$

in minutes where the cost is a time, with $S_1$ only for registered exhibitors and
$\lvert\mathcal{B}\rvert$ the number of blocks. Its S2, S3, S9, S10 and block-count terms and
weights are those of the solver's structure phase. The validator takes a program's lunches as
published, or else infers each from the judge's longest lunch-length gap, so for a human program
its S10 is an estimate. Which form stays is an open question (SPEC §7).

## 7. What the solver actually solves

The reference problem chooses each judge's running order $\pi_j$ freely. Giving every event its
own position in a CP-SAT circuit would cost 2,000–4,800 arcs per day, each with a constraint per
ring. The solver therefore restricts the running order in three steps, then solves everything else
exactly:

1. **Target group order.** A dynamic program over the $2^7$ subsets of groups finds the group order
   that achieves the judge-chain lower bound (`solve/bounds.py`). It sets priorities only.
2. **Segments.** Each judge's events are split into **segments**, one per (group, equipment): e.g.
   "this judge's Terrier table breeds". The solver chooses the order of a judge's segments (a
   circuit per judge), with each group's segments contiguous. So a judge's group order and, within
   a group, the order of ground, table and ramp breeds are decisions; S4 holds by construction, and
   table and ramp breeds stay consecutive within a group.
3. **Packing.** Within each segment, the order of the breeds is fixed beforehand by a small
   bin-packing model (`solve/packing.py`): pack the segment's breeds into bins of at most $\kappa$,
   fewest short bins first, then fewest bins, keeping the varieties of one breed together (as humans
   do 82% of the time, E). Bins are laid out fullest first.

Everything else is a decision of one CP-SAT model (`solve/cpsat.py`, step 3c): the segment order,
every cut, every ring, every start, every lunch, the group order and starts, and BIS, under every
hard constraint of section 4, with the phases of section 6. Time is in tenths of a minute, so 2.4
min/dog is exact, with a horizon of 18 hours after $T_0$. The cuts inside a segment remain free, so
the packing's bins are a suggestion: the model can still cut elsewhere.

**Two approximations** in the solver, which the validator counts exactly:

- S3 between *different* judges is counted only at each judge's first and last breed of the day
  (and at pinned blocks' ends). A judge who changes rings mid-day is not counted at that change.
- S1 leaves out pairs of events of the same judge, which cannot overlap, and pairs of pinned
  events, which no decision changes.

## 8. How good is a solution?

Each phase reports its proven bound. For the finish there are also two **lower bounds**, which no
valid program can beat:

- **Combinatorial** (`solve/bounds.py`): the larger of a judge-chain bound (a DP over subsets of
  groups, with lunches and H13–H14) and a ring-capacity bound (|R| rings before the first group,
  |R| − |R_G| after it).
- **Relaxation** (`solve/relaxation.py`): a CP-SAT model that keeps each judge judging one event
  at a time, lunch in the window with judging on both sides, the rings as a pool of |R| (|R| −
  |R_G| from the first group on, for H9), pinned blocks, and H7, H8, H10, H13 and H14 exactly. It
  drops blocks (H1, H5), the grid, travel, ring identity, running order and equipment, and rounds
  durations down. It is solved to proven optimality in seconds, and its optimum bounds every valid
  program. The larger of the two bounds is reported.

Neither bound uses the closing time $T_{\text{end}}$: it limits how late a program may finish,
not how early, so the bounds stay valid with or without it.

Step 3c's finish is within 0–10 minutes of the lower bound on every one of the 14 published
days (mean 4; SOLUTION.md §4), and proven optimal for the restricted problem on 13 of them. The gap measures both the restriction of section 7 and the bound's own
weakness, so the restricted problem is not losing much.

## 9. Modeling choices and why

**Planned times, not real times.** Every duration is planned: entries × time per dog. Real judging
runs faster or slower, and absent dogs shorten it. Published programs are planned the same way (E:
the median published gap is exactly 2.40 min/dog), so a model of planned times describes what a
superintendent actually publishes. Robustness to delays is not modeled.

**25 dogs an hour.** AKC Rules Ch.7 §12 (25 dogs/hr) and Show Manual §6 (20/hr for permit judges).
A judge's own figure overrides it.

**Events as the unit.** A breed or variety is judged without interruption, and every rule about
order (breeds before groups, table breeds together) is about breeds, so events are the atoms.
Classes inside a breed are not scheduled: their order is fixed by AKC rules (H15) and only their
total length matters, except for the champions' window in S1.

**Blocks decided by cuts.** Choosing where to cut a judge's running order, rather than choosing
from a list of candidate blocks, keeps block formation and placement in one model: cutting
elsewhere can be what lets a block fit a ring or a lunch.

**The group ring and the groups.** Group durations assume one Best of Breed winner per breed
competes in its group (U). That overstates groups, since not every winner competes; on the 11
published days the modeled groups and BIS take 5–7½ hours, so modeled end times run past real
ones and are not a calibration target (U). Two group rings act as one (U).

**Strict where humans bend.** H9 (both group rings clear) and H14 (groups after every lunch) are
hard even though humans bend H9 on 2 of 14 days, because the user decided so (U). The final
segment rule (AKC: at most 50 dogs) is a soft cost (S8), because humans exceed it on 4 of 14 days
and the user chose to follow practice (U, E).

**The lunch window is hard.** A lunch must start in the window because the window reflects when
food is served (U). Making it soft would let a heavily loaded judge lunch at 2 PM.

**Conflicts only for those who opt in.** The conflict-resolution program is an accommodation
exhibitors sign up for (U). Without exhibitor information, or with no one registered, S1 is
absent and the program is planned as if conflicts didn't exist (U). Exhibitor data for testing is
synthetic, since published programs have none.

**Margins.** Judges and exhibitors take 2 minutes to change rings (U, adjustable). The margin is
what makes back-to-back events in different rings a conflict for an exhibitor.

**Only regular breed events.** Sweepstakes, Junior Showmanship, Beginner Puppy, FSS open shows,
concurrent specialties, obedience and rally are out of scope (U 2026-11-13): the domain model
cannot represent them.

## 10. Not modeled

- **Ring geography.** Ring adjacency and distances are not available, so a judge's move costs the
  same between any two rings, and two group rings are assumed adjacent.
- **Choosing the group rings.** They are an input (open question, SPEC §7).
- **Judge assignment.** Who judges what is an input; reassigning an overloaded judge is out of scope.
- **Short-nosed breeds in the cool of the day, and permit judges at times the AKC field
  representative can attend** (S6, S7): neither has data.
- **Why humans start groups later.** Under these rules even the greedy baseline starts groups
  15–75 minutes before humans do on most days (SPEC §8). Something that holds humans back is not yet in the model;
  candidates are handlers who show group dogs while breed judging continues, and convention.
