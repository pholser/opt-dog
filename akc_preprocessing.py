"""
akc_preprocessing.py
====================
Phase B preprocessing pipeline for the AKC show scheduling optimizer.

Reads a validated show workbook, applies all domain logic, and produces a
ShowData object ready for consumption by the Phase C MIP.

Usage
-----
    from akc_preprocessing import load_show

    show = load_show("my_show.xlsx")          # raises PreprocessingError on any problem
    show = load_show("my_show.xlsx", strict=False)  # warnings only, no exceptions

    # Inspect results
    print(show.summary())
    for seg in show.segments:
        print(seg)

Public API
----------
    load_show(path, strict=True) -> ShowData
    ShowData                     -- top-level result object
    ShowParams                   -- show-level timing parameters
    RingInfo                     -- one ring
    GroupInfo                    -- one AKC group
    BreedInfo                    -- one breed with computed durations
    JudgeInfo                    -- one judge with resolved rate
    SegmentInfo                  -- one scheduling segment (packed breeds)
    ConflictPair                 -- one exhibitor conflict pair
    PreprocessingError           -- raised on validation failure (strict mode)
    PreprocessingWarning         -- issued for advisory conditions
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Exceptions and warnings
# ---------------------------------------------------------------------------

class PreprocessingError(Exception):
    """Fatal validation failure. Raised when strict=True (the default)."""


class PreprocessingWarning(UserWarning):
    """Advisory condition that does not prevent model construction."""


# ---------------------------------------------------------------------------
# AKC constants
# ---------------------------------------------------------------------------

STANDARD_RATE_MPD   = 60 / 25        # minutes per dog, standard judge
PERMIT_RATE_MPD     = 60 / 20        # minutes per dog, permit judge

JUDGE_ENTRY_LIMIT   = 175            # hard AKC limit
GROUP_ENTRY_WARNING = 150            # advisory: group judges should stay under this

MANDATORY_BREAK_THRESHOLD_MIN = 300  # 5 hours → mandatory lunch break

# Segment sizing rules (AKC Show Manual, Section 6):
#   - Each judge's breeds divided into ~1-hour periods
#   - 1-hour target: soft cap = floor(60 / rate_mpd) dogs for that judge's rate
#     Standard: floor(60 / 2.4) = 25 dogs/hr  -> soft cap ~25
#     Permit:   floor(60 / 3.0) = 20 dogs/hr  -> soft cap ~20
#   - Hard cap of 50 dogs per segment UNLESS it is a single breed/variety
#     (a breed with >30 entries may occupy its own unlimited segment)
#   - Final segment may be expanded but may not exceed 50 (multi-breed)
SEGMENT_HARD_MAX    = 50             # multi-breed segments: never exceed this
BREED_SPLIT_THRESH  = 30             # single breed >30 dogs: exempt from hard cap

# Equipment type order for within-judge sorting (minimise switches between segments)
EQUIP_ORDER = {"table": 0, "ramp": 1, "floor": 2}

# Group and BIS judging durations
# Group judging: allow ~2.5 minutes per entered breed (BOB winner evaluated).
# Minimum of 15 minutes regardless of entry count.
# BIS is fixed at ~20 minutes (7 group winners + placements).
GROUP_MINS_PER_BREED  = 2.5
GROUP_JUDGING_MIN_MIN = 15      # floor: never schedule less than this
BIS_JUDGING_MINUTES   = 20


# ---------------------------------------------------------------------------
# Data classes — public API
# ---------------------------------------------------------------------------

@dataclass
class ShowParams:
    """Show-level timing and discretization parameters."""
    show_id:            str
    club_name:          str
    venue_name:         str
    venue_address:      str
    show_date:          str
    judging_start_slot: int     # absolute slots from midnight; display use only
    lunch_start_slot:   int
    lunch_end_slot:     int
    lunch_duration_slots: int
    slot_minutes:       int
    indoor:             bool
    notes:              str

    def slots(self, minutes: float) -> int:
        """Convert a duration in minutes to an integer number of time slots."""
        return math.ceil(minutes / self.slot_minutes)

    def time_to_slot(self, hhmm: str) -> int:
        """Convert HH:MM string to absolute slot number from midnight."""
        h, m = int(hhmm[:2]), int(hhmm[3:])
        return (h * 60 + m) // self.slot_minutes

    def slot_to_hhmm(self, slot: int) -> str:
        """Convert absolute slot number back to HH:MM string."""
        minutes = slot * self.slot_minutes
        return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class RingInfo:
    ring_id:      str
    is_group_ring: bool
    width_ft:     float
    length_ft:    float
    notes:        str


@dataclass
class GroupInfo:
    group_id:   str
    group_name: str
    judge_id:   str
    breed_ids:  List[str] = field(default_factory=list)
    # Duration in slots — set after all breed durations known
    judging_duration_slots: int = 0


@dataclass
class JudgeInfo:
    judge_id:            str
    judge_name:          str
    is_permit:           bool
    rate_mpd:            float    # resolved minutes-per-dog
    is_bis_judge:        bool     = False
    group_ids:           List[str] = field(default_factory=list)
    breed_ids:           List[str] = field(default_factory=list)
    total_breed_entries: int       = 0
    requires_lunch_break: bool     = False


@dataclass
class BreedInfo:
    breed_id:            str
    breed_name:          str
    variety:             str       # "" if none
    group_id:            str
    equipment_type:      str       # table | ramp | floor
    judge_id:            str

    # Raw entry counts
    n_class_dogs:        int
    n_class_bitches:     int
    n_specials_dogs:     int
    n_specials_bitches:  int
    n_nonregular:        int
    nonregular_position: str       # before_specials | after_specials

    # Derived totals
    n_class:             int = 0
    n_specials:          int = 0
    n_total:             int = 0

    # Computed durations (in time slots)
    delta_class_slots:   int = 0
    delta_nr_slots:      int = 0
    delta_specials_slots: int = 0
    delta_total_slots:   int = 0

    # Phase ordering: list of phase names in judging order
    # e.g. ["class", "nonregular", "specials"] or ["class", "specials", "nonregular"]
    phase_order:         List[str] = field(default_factory=list)

    def display_name(self) -> str:
        return f"{self.breed_name} ({self.variety})" if self.variety else self.breed_name

    def __post_init__(self):
        self.n_class    = self.n_class_dogs + self.n_class_bitches
        self.n_specials = self.n_specials_dogs + self.n_specials_bitches
        self.n_total    = self.n_class + self.n_specials + self.n_nonregular


@dataclass
class SegmentInfo:
    """
    A contiguous block of breeds assigned to a single judge in a single ring.

    breed_ids is ordered — the MIP may permute this ordering (via y variables)
    to resolve conflicts, subject to the equipment-type grouping being preserved
    within the segment. The initial ordering here is equipment-sorted.
    """
    segment_id:         str
    judge_id:           str
    breed_ids:          List[str]          # ordered list of breed IDs
    equipment_sequence: List[str]          # equipment type per breed, same order
    duration_slots:     int                # sum of delta_total_slots
    n_dogs:             int                # total dogs in segment
    has_equipment_mix:  bool               # True if >1 equipment type present


@dataclass
class ConflictPair:
    """
    A pair of dogs handled by the same opted-in handler in different breeds.
    The MIP minimises the number of pairs whose conflict windows overlap.
    """
    handler_id:   str
    dog_id_a:     str
    dog_id_b:     str
    breed_id_a:   str
    breed_id_b:   str
    entry_type_a: str     # class | specials | nonregular
    entry_type_b: str

    def window_description(self) -> str:
        def w(entry_type, breed_id):
            if entry_type == "class":
                return f"[start_{breed_id}, start_{breed_id}+delta_class]"
            elif entry_type == "specials":
                return f"[start_{breed_id}+delta_class, start_{breed_id}+delta_class+delta_specials]"
            else:
                return f"[start_{breed_id}, start_{breed_id}+delta_total]"
        return f"{w(self.entry_type_a, self.breed_id_a)} vs {w(self.entry_type_b, self.breed_id_b)}"


@dataclass
class ShowData:
    """
    Complete preprocessed show data, ready for MIP construction.

    All durations are expressed in time slots.  Slot 0 = judging_start.
    Absolute slot indices are relative to midnight (matching ShowParams).
    """
    params:         ShowParams
    rings:          Dict[str, RingInfo]         # ring_id → RingInfo
    ring_distances: Dict[Tuple[str,str], float]  # (id_a, id_b) → feet, a<b
    groups:         Dict[str, GroupInfo]         # group_id → GroupInfo
    judges:         Dict[str, JudgeInfo]         # judge_id → JudgeInfo
    breeds:         Dict[str, BreedInfo]         # breed_id → BreedInfo
    segments:       List[SegmentInfo]
    conflict_pairs: List[ConflictPair]
    bis_judge_id:   str

    # Advisory messages accumulated during preprocessing
    warnings:       List[str] = field(default_factory=list)

    @property
    def group_rings(self) -> List[str]:
        return [rid for rid, r in self.rings.items() if r.is_group_ring]

    @property
    def breed_rings(self) -> List[str]:
        return [rid for rid, r in self.rings.items() if not r.is_group_ring]

    @property
    def judges_requiring_lunch(self) -> List[str]:
        return [jid for jid, j in self.judges.items() if j.requires_lunch_break]

    def summary(self) -> str:
        lines = [
            f"Show: {self.params.club_name} — {self.params.show_date}",
            f"  Venue : {self.params.venue_name}",
            f"  Timing: starts {self.params.slot_to_hhmm(self.params.judging_start_slot)}"
            f"  ({self.params.slot_minutes} min/slot)",
            f"  Lunch : {self.params.slot_to_hhmm(self.params.lunch_start_slot)}"
            f" – {self.params.slot_to_hhmm(self.params.lunch_end_slot)}"
            f"  ({self.params.lunch_duration_slots} slots required)",
            "",
            f"  Breeds   : {len(self.breeds)}",
            f"  Judges   : {len(self.judges)}  "
            f"({len(self.judges_requiring_lunch)} require mandatory lunch break)",
            f"  Rings    : {len(self.rings)}  "
            f"({len(self.group_rings)} group rings)",
            f"  Segments : {len(self.segments)}",
            f"  Conflict pairs: {len(self.conflict_pairs)}",
        ]
        if self.warnings:
            lines += ["", f"  Warnings ({len(self.warnings)}):"]
            for w in self.warnings:
                lines.append(f"    ⚠  {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_show(path: str | Path, strict: bool = True,
              slot_minutes: int | None = None) -> ShowData:
    """
    Read and preprocess a show workbook.

    Parameters
    ----------
    path   : path to the .xlsx workbook produced by akc_show_generator.py
    strict : if True (default), any validation error raises PreprocessingError.
             if False, errors are demoted to warnings and processing continues
             where possible.

    Returns
    -------
    ShowData — fully preprocessed show ready for MIP construction.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")

    wb = _read_workbook(path)
    ctx = _PreprocessingContext(strict=strict)

    params         = _parse_show(wb, ctx, slot_minutes=slot_minutes)
    rings, dists   = _parse_rings(wb, ctx, params)
    groups         = _parse_groups(wb, ctx)
    judges         = _parse_judges(wb, ctx)
    breeds         = _parse_breeds(wb, ctx, params, judges, groups)
    bis_judge_id   = _parse_bis(wb, ctx, judges)

    _link_groups(wb, ctx, groups, judges)
    _link_breeds_to_judges(wb, ctx, breeds, judges, groups, bis_judge_id, params)
    _compute_judge_lunch(judges, params)
    _validate_akc_rules(ctx, breeds, judges, groups, bis_judge_id)

    segments       = _pack_segments(breeds, judges, params, ctx)
    conflict_pairs = _enumerate_conflict_pairs(wb, ctx, breeds)

    data = ShowData(
        params         = params,
        rings          = rings,
        ring_distances = dists,
        groups         = groups,
        judges         = judges,
        breeds         = breeds,
        segments       = segments,
        conflict_pairs = conflict_pairs,
        bis_judge_id   = bis_judge_id,
        warnings       = ctx.warnings,
    )

    for w in ctx.warnings:
        warnings.warn(w, PreprocessingWarning, stacklevel=2)

    return data


# ---------------------------------------------------------------------------
# Internal: workbook reader
# ---------------------------------------------------------------------------

def _read_workbook(path: Path) -> Dict[str, pd.DataFrame]:
    """Read all sheets into a dict of DataFrames with string dtypes."""
    required_sheets = {
        "Show", "Rings", "RingDistances", "Groups", "Breeds",
        "BreedEntries", "Judges", "BreedJudgeAssignments",
        "GroupJudgeAssignments", "BISJudgeAssignment", "Handlers", "Dogs",
    }
    xl = pd.read_excel(path, sheet_name=None, dtype=str)
    missing = required_sheets - set(xl.keys())
    if missing:
        raise PreprocessingError(f"Workbook missing required sheets: {sorted(missing)}")
    # Strip whitespace from all string cells
    for name, df in xl.items():
        xl[name] = df.apply(lambda col: col.str.strip() if col.dtype == object else col)
    return xl


# ---------------------------------------------------------------------------
# Internal: context (error/warning accumulator)
# ---------------------------------------------------------------------------

class _PreprocessingContext:
    def __init__(self, strict: bool):
        self.strict   = strict
        self.warnings: List[str] = []

    def error(self, msg: str):
        if self.strict:
            raise PreprocessingError(msg)
        self.warn(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


# ---------------------------------------------------------------------------
# Internal: parsers
# ---------------------------------------------------------------------------

def _parse_show(wb: dict, ctx: _PreprocessingContext,
                slot_minutes: int | None = None) -> ShowParams:
    df = wb["Show"]
    if len(df) != 1:
        ctx.error(f"Show sheet must have exactly 1 row; found {len(df)}")

    row = df.iloc[0]

    def req(col):
        v = row.get(col, "")
        if pd.isna(v) or str(v).strip() == "":
            ctx.error(f"Show.{col} is required but missing")
        return str(v).strip()

    slot_min = slot_minutes if slot_minutes is not None else int(req("time_slot_minutes"))

    def hhmm_to_slot(col):
        v = req(col)
        # accept HH:MM or H:MM
        parts = v.split(":")
        return (int(parts[0]) * 60 + int(parts[1])) // slot_min

    js  = hhmm_to_slot("judging_start")
    ls  = hhmm_to_slot("lunch_window_start")
    le  = hhmm_to_slot("lunch_window_end")
    ld_min = int(req("lunch_duration_min"))

    if ls < js:
        ctx.error("lunch_window_start must be at or after judging_start")
    if ls >= le:
        ctx.error("lunch_window_start must be before lunch_window_end")

    return ShowParams(
        show_id               = req("show_id"),
        club_name             = req("club_name"),
        venue_name            = req("venue_name"),
        venue_address         = row.get("venue_address", ""),
        show_date             = req("show_date"),
        judging_start_slot    = js,
        lunch_start_slot      = ls,
        lunch_end_slot        = le,
        lunch_duration_slots  = math.ceil(ld_min / slot_min),
        slot_minutes          = slot_min,
        indoor                = str(row.get("indoor", "True")).strip().upper() == "TRUE",
        notes                 = str(row.get("notes", "") or "").strip(),
    )


def _parse_rings(wb, ctx, params) -> Tuple[Dict[str, RingInfo], Dict[Tuple[str,str], float]]:
    rings = {}
    for _, row in wb["Rings"].iterrows():
        rid = str(row["ring_id"]).strip()
        rings[rid] = RingInfo(
            ring_id       = rid,
            is_group_ring = str(row.get("is_group_ring", "False")).strip().upper() == "TRUE",
            width_ft      = float(row["width_ft"]) if pd.notna(row.get("width_ft")) else 0.0,
            length_ft     = float(row["length_ft"]) if pd.notna(row.get("length_ft")) else 0.0,
            notes         = str(row.get("notes", "") or "").strip(),
        )

    if not any(r.is_group_ring for r in rings.values()):
        ctx.error("No group rings defined. At least one ring must have is_group_ring = True.")
    if not any(not r.is_group_ring for r in rings.values()):
        ctx.error("All rings are group rings. At least one non-group ring is required.")

    dists = {}
    for _, row in wb["RingDistances"].iterrows():
        ra, rb = str(row["ring_id_a"]).strip(), str(row["ring_id_b"]).strip()
        if ra not in rings:
            ctx.warn(f"RingDistances references unknown ring_id_a={ra!r}")
        if rb not in rings:
            ctx.warn(f"RingDistances references unknown ring_id_b={rb!r}")
        key = (min(ra, rb), max(ra, rb))
        dists[key] = float(row["distance_ft"]) if pd.notna(row.get("distance_ft")) else 0.0

    return rings, dists


def _parse_groups(wb, ctx) -> Dict[str, GroupInfo]:
    groups = {}
    for _, row in wb["Groups"].iterrows():
        gid = str(row["group_id"]).strip()
        groups[gid] = GroupInfo(
            group_id   = gid,
            group_name = str(row["group_name"]).strip(),
            judge_id   = "",   # filled in by _link_groups
        )
    if not groups:
        ctx.error("No groups defined.")
    return groups


def _parse_judges(wb, ctx) -> Dict[str, JudgeInfo]:
    judges = {}
    for _, row in wb["Judges"].iterrows():
        jid  = str(row["judge_id"]).strip()
        is_permit = str(row.get("is_permit", "False")).strip().upper() == "TRUE"
        override_raw = row.get("judging_rate_override", "")
        if pd.notna(override_raw) and str(override_raw).strip() not in ("", "nan"):
            rate_mpd = float(override_raw)
        else:
            rate_mpd = PERMIT_RATE_MPD if is_permit else STANDARD_RATE_MPD
        judges[jid] = JudgeInfo(
            judge_id   = jid,
            judge_name = str(row["judge_name"]).strip(),
            is_permit  = is_permit,
            rate_mpd   = rate_mpd,
        )
    if not judges:
        ctx.error("No judges defined.")
    return judges


def _parse_breeds(wb, ctx, params, judges, groups) -> Dict[str, BreedInfo]:
    """Parse Breeds + BreedEntries, compute all durations."""
    breed_df  = wb["Breeds"].set_index("breed_id")
    entry_df  = wb["BreedEntries"].set_index("breed_id")
    bja_df    = wb["BreedJudgeAssignments"].set_index("breed_id")

    # Referential integrity
    missing_entries = set(breed_df.index) - set(entry_df.index)
    if missing_entries:
        ctx.error(f"Breeds missing BreedEntries rows: {sorted(missing_entries)}")

    missing_bja = set(breed_df.index) - set(bja_df.index)
    if missing_bja:
        ctx.error(f"Breeds missing BreedJudgeAssignments rows: {sorted(missing_bja)}")

    breeds = {}
    for bid, brow in breed_df.iterrows():
        bid = str(bid).strip()

        if bid not in entry_df.index:
            continue   # already reported above

        erow = entry_df.loc[bid]
        if bid in bja_df.index:
            judge_id = str(bja_df.loc[bid, "judge_id"]).strip()
        else:
            judge_id = ""

        if judge_id not in judges:
            ctx.error(f"Breed {bid}: judge_id {judge_id!r} not found in Judges sheet")
            judge_id = next(iter(judges))   # fallback

        judge = judges[judge_id]

        def int_col(col, default=0):
            v = erow.get(col, default)
            return int(v) if pd.notna(v) and str(v).strip() not in ("", "nan") else default

        n_cd  = int_col("n_class_dogs")
        n_cb  = int_col("n_class_bitches")
        n_sd  = int_col("n_specials_dogs")
        n_sb  = int_col("n_specials_bitches")
        n_nr  = int_col("n_nonregular")
        nr_pos_raw = str(erow.get("nonregular_position", "after_specials") or "after_specials").strip()
        if nr_pos_raw not in ("before_specials", "after_specials"):
            ctx.warn(f"Breed {bid}: unrecognised nonregular_position {nr_pos_raw!r}; defaulting to after_specials")
            nr_pos_raw = "after_specials"
        nr_pos = nr_pos_raw

        n_class    = n_cd + n_cb
        n_specials = n_sd + n_sb
        n_total    = n_class + n_specials + n_nr

        if n_total == 0:
            ctx.error(f"Breed {bid} ({brow['breed_name']}) has zero total entries")

        # Duration computation (in slots)
        #   delta_class   = ceil(n_class   * rate_mpd / slot_min)
        #   delta_nr      = ceil(n_nr      * rate_mpd / slot_min)
        #   delta_specials = ceil((n_specials + 1) * rate_mpd / slot_min)  [+1 for BOB]
        slot = params.slot_minutes

        def to_slots(n_dogs: float) -> int:
            return math.ceil(n_dogs * judge.rate_mpd / slot)

        d_class    = to_slots(n_class)
        d_nr       = to_slots(n_nr)       if n_nr > 0 else 0
        d_specials = to_slots(n_specials + 1)   # +1 accounts for BOB judging
        d_total    = d_class + d_nr + d_specials

        # Within-breed phase ordering
        if n_nr == 0:
            phase_order = ["class", "specials"]
        elif nr_pos == "before_specials":
            phase_order = ["class", "nonregular", "specials"]
        else:
            phase_order = ["class", "specials", "nonregular"]

        equip = str(brow.get("equipment_type", "floor")).strip()
        if equip not in EQUIP_ORDER:
            ctx.warn(f"Breed {bid}: unknown equipment_type {equip!r}; treating as floor")
            equip = "floor"

        gid = str(brow.get("group_id", "")).strip()
        if gid not in groups:
            ctx.error(f"Breed {bid}: group_id {gid!r} not found in Groups")

        breeds[bid] = BreedInfo(
            breed_id             = bid,
            breed_name           = str(brow["breed_name"]).strip(),
            variety              = str(brow.get("variety", "") or "").strip(),
            group_id             = gid,
            equipment_type       = equip,
            judge_id             = judge_id,
            n_class_dogs         = n_cd,
            n_class_bitches      = n_cb,
            n_specials_dogs      = n_sd,
            n_specials_bitches   = n_sb,
            n_nonregular         = n_nr,
            nonregular_position  = nr_pos,
            n_class              = n_class,
            n_specials           = n_specials,
            n_total              = n_total,
            delta_class_slots    = d_class,
            delta_nr_slots       = d_nr,
            delta_specials_slots = d_specials,
            delta_total_slots    = d_total,
            phase_order          = phase_order,
        )

    return breeds


def _parse_bis(wb, ctx, judges) -> str:
    df = wb["BISJudgeAssignment"]
    if len(df) != 1:
        ctx.error(f"BISJudgeAssignment must have exactly 1 row; found {len(df)}")
    jid = str(df.iloc[0]["judge_id"]).strip()
    if jid not in judges:
        ctx.error(f"BIS judge {jid!r} not found in Judges")
    judges[jid].is_bis_judge = True
    return jid


def _link_groups(wb, ctx, groups, judges):
    """Assign judge_id to each group and record group_ids on each judge."""
    gja = wb["GroupJudgeAssignments"]
    seen_groups = set()
    for _, row in gja.iterrows():
        gid = str(row["group_id"]).strip()
        jid = str(row["judge_id"]).strip()
        if gid not in groups:
            ctx.error(f"GroupJudgeAssignments references unknown group_id {gid!r}")
            continue
        if jid not in judges:
            ctx.error(f"GroupJudgeAssignments references unknown judge_id {jid!r}")
            continue
        if gid in seen_groups:
            ctx.error(f"Group {gid} has more than one GroupJudgeAssignment")
        seen_groups.add(gid)
        groups[gid].judge_id = jid
        judges[jid].group_ids.append(gid)

    missing = [gid for gid, g in groups.items() if not g.judge_id]
    if missing:
        ctx.error(f"Groups without a judge assignment: {sorted(missing)}")


def _link_breeds_to_judges(wb, ctx, breeds, judges, groups, bis_judge_id, params):
    """
    Populate judge.breed_ids, judge.total_breed_entries.
    Attach breed_ids to their group.
    """
    for bid, b in breeds.items():
        jid = b.judge_id
        if jid in judges:
            judges[jid].breed_ids.append(bid)
            judges[jid].total_breed_entries += b.n_total
        if b.group_id in groups:
            groups[b.group_id].breed_ids.append(bid)

    # Set group judging duration: proportional to the number of entered breeds,
    # rounded up to the nearest slot, with a minimum floor.
    for g in groups.values():
        n_breeds  = len(g.breed_ids)
        raw_min   = n_breeds * GROUP_MINS_PER_BREED
        floored   = max(raw_min, GROUP_JUDGING_MIN_MIN)
        g.judging_duration_slots = math.ceil(floored / params.slot_minutes)


def _compute_judge_lunch(judges, params):
    """Determine which judges require a mandatory lunch break."""
    for j in judges.values():
        if j.is_bis_judge and not j.breed_ids and not j.group_ids:
            continue  # BIS-only judge, no extended assignment
        total_min = j.total_breed_entries * j.rate_mpd
        if total_min > MANDATORY_BREAK_THRESHOLD_MIN:
            j.requires_lunch_break = True


def _validate_akc_rules(ctx, breeds, judges, groups, bis_judge_id):
    """Validate AKC judge conflict and entry limit rules."""
    # Rule: judge cannot do group + BIS
    bis_judge = judges.get(bis_judge_id)
    if bis_judge and bis_judge.group_ids:
        ctx.error(
            f"AKC violation: BIS judge {bis_judge_id} ({bis_judge.judge_name}) "
            f"is also assigned group(s): {bis_judge.group_ids}"
        )

    # Rule: no judge assigned to breed + its group + BIS simultaneously
    for jid, j in judges.items():
        if not j.group_ids:
            continue
        for gid in j.group_ids:
            # Check if this judge also has any breed in this group
            group_breeds = [bid for bid, b in breeds.items()
                            if b.group_id == gid and b.judge_id == jid]
            if group_breeds and jid == bis_judge_id:
                ctx.error(
                    f"AKC violation: Judge {jid} ({j.judge_name}) judges "
                    f"breed(s) in group {gid}, the group itself, AND BIS."
                )

    # Entry limits
    for jid, j in judges.items():
        if j.total_breed_entries > JUDGE_ENTRY_LIMIT:
            ctx.error(
                f"Judge {jid} ({j.judge_name}) has {j.total_breed_entries} breed entries "
                f"> AKC limit of {JUDGE_ENTRY_LIMIT}."
            )
        if j.group_ids and j.total_breed_entries > GROUP_ENTRY_WARNING:
            ctx.warn(
                f"Judge {jid} ({j.judge_name}) has a group assignment and "
                f"{j.total_breed_entries} breed entries > recommended {GROUP_ENTRY_WARNING}."
            )


# ---------------------------------------------------------------------------
# Internal: segment packing
# ---------------------------------------------------------------------------

def _pack_segments(breeds, judges, params, ctx) -> List[SegmentInfo]:
    """
    Pack each judge's breeds into segments using an equipment-aware greedy
    bin-packing algorithm.

    AKC Show Manual Section 6 rules implemented:
      - Soft cap: ~1 hour of judging per segment.
        At each judge's rate: soft_cap = floor(60 / rate_mpd) dogs.
        Standard (2.4 mpd) → 25 dogs; Permit (3.0 mpd) → 20 dogs.
      - Hard cap: 50 dogs per segment for multi-breed segments.
      - Single-breed exception: a breed with >BREED_SPLIT_THRESH (30) entries
        is placed alone in its own segment with no hard cap applied.
      - Equipment-break rule: open a new segment at an equipment switch when
        the current segment is already at or past the soft cap.
      - Final segment of a judge's assignment may be expanded up to the hard
        cap, but no further (unless it is a single oversized breed).
    """
    segments = []
    seg_counter = 1

    for jid, judge in judges.items():
        if not judge.breed_ids:
            continue

        # Soft cap is time-based: how many dogs fit in ~60 minutes at this rate
        soft_cap = max(1, int(60 / judge.rate_mpd))

        # Step 1 & 2: sort breeds by (equipment_order, n_total desc)
        judge_breeds = [breeds[bid] for bid in judge.breed_ids if bid in breeds]
        judge_breeds.sort(key=lambda b: (EQUIP_ORDER.get(b.equipment_type, 99), -b.n_total))

        # Step 3: greedy packing
        current_ids:   List[str] = []
        current_equip: List[str] = []
        current_dogs:  int       = 0
        current_slots: int       = 0

        def flush_segment():
            nonlocal current_ids, current_equip, current_dogs, current_slots, seg_counter
            if not current_ids:
                return
            equip_set = set(current_equip)
            segments.append(SegmentInfo(
                segment_id        = f"SEG{seg_counter:04d}",
                judge_id          = jid,
                breed_ids         = list(current_ids),
                equipment_sequence= list(current_equip),
                duration_slots    = current_slots,
                n_dogs            = current_dogs,
                has_equipment_mix = len(equip_set) > 1,
            ))
            seg_counter   += 1
            current_ids    = []
            current_equip  = []
            current_dogs   = 0
            current_slots  = 0

        for b in judge_breeds:
            # Single oversized breed: flush whatever we have and give it its
            # own segment.  No hard cap applies (AKC single-breed exception).
            if b.n_total > BREED_SPLIT_THRESH:
                flush_segment()
                current_ids.append(b.breed_id)
                current_equip.append(b.equipment_type)
                current_dogs  += b.n_total
                current_slots += b.delta_total_slots
                flush_segment()
                continue

            # For multi-breed segments: enforce hard cap and soft/equip split.
            # Break if:
            #   (a) adding this breed would exceed the hard cap (50 dogs), or
            #   (b) we're past the soft cap (~1 hr) AND this breed would push
            #       us further — always break here, not just at equip switches.
            #       Exception: if current segment has only one breed so far,
            #       don't break (avoids 0-dog segments for large single breeds
            #       that slipped under BREED_SPLIT_THRESH).
            would_exceed_hard = (current_dogs + b.n_total) > SEGMENT_HARD_MAX
            past_soft         = current_dogs >= soft_cap
            equip_switch      = current_equip and current_equip[-1] != b.equipment_type

            should_break = (
                would_exceed_hard
                or (past_soft and len(current_ids) >= 1)
            )

            if current_ids and should_break:
                flush_segment()

            current_ids.append(b.breed_id)
            current_equip.append(b.equipment_type)
            current_dogs  += b.n_total
            current_slots += b.delta_total_slots

        flush_segment()

    return segments


# ---------------------------------------------------------------------------
# Internal: conflict pair enumeration
# ---------------------------------------------------------------------------

def _enumerate_conflict_pairs(wb, ctx, breeds) -> List[ConflictPair]:
    """
    For each opted-in handler, enumerate all cross-breed dog pairs they handle.
    Only pairs in *different* breeds generate conflict constraints.
    """
    handler_df = wb["Handlers"]
    dog_df     = wb["Dogs"]

    opted_in_handlers = set(
        str(row["handler_id"]).strip()
        for _, row in handler_df.iterrows()
        if str(row.get("conflict_opt_in", "False")).strip().upper() == "TRUE"
    )

    if not opted_in_handlers:
        return []

    # Build handler → list of (dog_id, breed_id, entry_type) for opted-in only
    handler_dogs: Dict[str, List[Tuple[str,str,str]]] = defaultdict(list)
    for _, row in dog_df.iterrows():
        hid = str(row["handler_id"]).strip()
        if hid not in opted_in_handlers:
            continue
        bid = str(row["breed_id"]).strip()
        if bid not in breeds:
            continue
        did        = str(row["dog_id"]).strip()
        entry_type = str(row.get("entry_type", "class")).strip()
        handler_dogs[hid].append((did, bid, entry_type))

    pairs = []
    for hid, dog_list in handler_dogs.items():
        # Enumerate all (i,j) pairs where i < j and breeds differ
        for i in range(len(dog_list)):
            for j in range(i + 1, len(dog_list)):
                did_a, bid_a, et_a = dog_list[i]
                did_b, bid_b, et_b = dog_list[j]
                if bid_a == bid_b:
                    continue   # same breed → no scheduling conflict
                pairs.append(ConflictPair(
                    handler_id   = hid,
                    dog_id_a     = did_a,
                    dog_id_b     = did_b,
                    breed_id_a   = bid_a,
                    breed_id_b   = bid_b,
                    entry_type_a = et_a,
                    entry_type_b = et_b,
                ))

    return pairs


# ---------------------------------------------------------------------------
# Pretty-printer helpers (for development / diagnostics)
# ---------------------------------------------------------------------------

def print_segments(show: ShowData, judge_id: Optional[str] = None):
    """Print a human-readable segment summary, optionally filtered by judge."""
    segs = show.segments
    if judge_id:
        segs = [s for s in segs if s.judge_id == judge_id]

    for seg in segs:
        j = show.judges[seg.judge_id]
        equip_summary = " / ".join(dict.fromkeys(seg.equipment_sequence))
        print(
            f"  {seg.segment_id}  judge={seg.judge_id} ({j.judge_name[:20]:<20s})  "
            f"dogs={seg.n_dogs:>3d}  slots={seg.duration_slots:>3d}  "
            f"equip=[{equip_summary}]  breeds={len(seg.breed_ids)}"
        )
        for bid, equip in zip(seg.breed_ids, seg.equipment_sequence):
            b = show.breeds[bid]
            print(
                f"      {bid}  {b.display_name()[:40]:<40s}  "
                f"{b.n_total:>3d} dogs  "
                f"{b.delta_total_slots:>3d} slots  [{equip}]"
                f"  phases={b.phase_order}"
            )


def print_conflict_pairs(show: ShowData, limit: int = 20):
    """Print a sample of conflict pairs."""
    print(f"Conflict pairs: {len(show.conflict_pairs)} total")
    for cp in show.conflict_pairs[:limit]:
        ba = show.breeds[cp.breed_id_a].display_name()
        bb = show.breeds[cp.breed_id_b].display_name()
        print(f"  Handler {cp.handler_id}: {ba} ({cp.entry_type_a}) vs {bb} ({cp.entry_type_b})")
    if len(show.conflict_pairs) > limit:
        print(f"  ... and {len(show.conflict_pairs) - limit} more")


# ---------------------------------------------------------------------------
# CLI — run directly for a quick diagnostic
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python akc_preprocessing.py <show.xlsx> [judge_id]")
        sys.exit(1)

    wb_path    = sys.argv[1]
    filter_jid = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Loading {wb_path} ...")
    show = load_show(wb_path)

    print()
    print(show.summary())
    print()
    print("=" * 70)
    print("SEGMENTS")
    print("=" * 70)
    print_segments(show, judge_id=filter_jid)
    print()
    print("=" * 70)
    print("CONFLICT PAIRS (sample)")
    print("=" * 70)
    print_conflict_pairs(show)

    print()
    print("Judges requiring mandatory lunch break:")
    for jid in show.judges_requiring_lunch:
        j = show.judges[jid]
        print(f"  {jid}  {j.judge_name}  ({j.total_breed_entries} entries, "
              f"{j.total_breed_entries * j.rate_mpd:.0f} min)")
