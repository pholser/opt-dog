"""Show-level parameters: rule thresholds, tolerances, margins and objective weights.

Defaults cite their source. Values marked "to calibrate" are starting points to be
replaced by measurements from real judging programs (see docs/calibration.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from optdog.domain import Minutes


@dataclass(frozen=True)
class ObjectiveWeights:
    """Weights for the weighted-sum objective (SPEC §6). All per unit stated."""

    finish: float = 1.0  # per minute of show finish (BIS end) after judging start
    conflict: float = 1.0  # per exhibitor-conflict minute (S1)
    ring_change: float = 10.0  # per judge ring change not at lunch (S2)
    ring_change_at_lunch: float = 2.0  # per judge ring change at lunch (S2)
    equipment_switch: float = 5.0  # per table/ramp switch within a ring (S3)
    group_switch: float = 2.0  # per extra group switch in a judge's breed order (S4)
    judge_idle: float = 0.5  # per idle minute in a judge's day, excluding lunch (S5)
    final_segment_excess: float = 2.0  # per dog over the final-segment limit (S8)


@dataclass(frozen=True)
class ShowParameters:
    # --- time grid
    slot_minutes: int = 5  # published times seen on 5-minute marks (E: Bradshaw 9:25, 10:10)

    # --- blocks (Rules Ch.7 §12)
    block_target_minutes: float = 60.0  # "periods of about one hour"
    block_tolerance_minutes: float = 15.0  # E: multi-breed blocks p90 27 dogs; 7 blocks > 75 min on 11 days
    single_breed_exception_entries: int = 30  # a breed with > 30 entries may be its own longer block
    final_segment_max_dogs: int = 50  # Rules: final segment ≤ 50 unless single breed; a SOFT cost here (U; humans exceed it)

    # --- judge breaks (Rules Ch.7 §12)
    lunch_required_after_minutes: float = 300.0  # assignment exceeding five hours
    lunch_min_minutes: int = 45
    lunch_max_minutes: int = 60
    lunch_window: tuple[Minutes, Minutes] = (11 * 60 + 30, 12 * 60 + 30)  # E: published lunches start 11:30–12:30 (p10–p90 11:30–12:20)

    # --- margins (U)
    exhibitor_travel_minutes: float = 2.0  # m_X between rings (U, adjustable)
    judge_travel_minutes: float = 2.0  # m_J between rings (U, adjustable)
    overlap_tolerance_minutes: float = 5.0  # E: at 25/hr, human blocks overrun the next published time by median 2.4, p90 4.8 min (~2 dogs)

    # --- groups (Show Manual §6)
    group_ring_setup_minutes: float = 0.0  # buffer between last breed in group ring(s) and first group

    # --- limits (Rules Ch.7 §13)
    judge_daily_limit: int = 175
    specialty_only_daily_limit: int = 200

    # --- show hours
    closing_time: Minutes | None = None  # posted closing time, if known

    weights: ObjectiveWeights = field(default_factory=ObjectiveWeights)
