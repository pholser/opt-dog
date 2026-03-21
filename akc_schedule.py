"""
akc_schedule.py — Shared types and post-processing for AKC show scheduling
===========================================================================

Contains solver-agnostic data classes and the post-hoc arena ring assignment
function used by all solver backends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Solver configuration
# ---------------------------------------------------------------------------

@dataclass
class SolveParams:
    """Solver configuration.  All times in minutes unless noted."""
    solver:         str   = "cpsat"
    time_limit_sec: int   = 300
    gap:            float = 0.01
    threads:        int   = 0
    tee:            bool  = True
    # Ring-switch penalty in minutes of BIS time.  Each ring switch a judge
    # makes is treated as costing this many minutes of finishing time.
    # 0 (default) → pure epsilon-hierarchy (switches are a tiebreaker only).
    # E.g. 15 → solver accepts up to 15 extra BIS minutes to eliminate one switch.
    ring_switch_penalty_min: float = 0.0
    # Hard constraint: forbid all ring switches entirely.
    # When True, ring_switch_penalty_min is ignored.
    forbid_ring_switches: bool = False


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class SegmentSchedule:
    segment_id: str
    judge_id:   str
    ring_id:    str
    start_slot: int     # absolute slot
    end_slot:   int     # absolute slot (exclusive)
    n_dogs:     int
    breed_ids:  List[str]


@dataclass
class GroupSchedule:
    group_id:   str
    judge_id:   str
    ring_id:    str
    start_slot: int
    end_slot:   int
    n_breeds:   int


@dataclass
class SolveResult:
    status:         str             # 'OPTIMAL' | 'FEASIBLE' | 'INFEASIBLE' | 'ERROR'
    gap:            float
    solve_time_sec: float
    bis_start_slot: int             # absolute slot
    segments:       List[SegmentSchedule] = field(default_factory=list)
    groups:         List[GroupSchedule]   = field(default_factory=list)
    lunch_slots:    Dict[str, int]        = field(default_factory=dict)
    equip_switches: int = 0
    ring_switches:  int = 0
    n_conflicts:    int = 0
    show:           object = None   # ShowData reference for program generation

    def summary(self) -> str:
        return (
            f"status={self.status}  gap={self.gap * 100:.1f}%  "
            f"time={self.solve_time_sec:.0f}s  "
            f"BIS_slot={self.bis_start_slot}  "
            f"ring_sw={self.ring_switches}  equip_sw={self.equip_switches}"
        )


# ---------------------------------------------------------------------------
# Post-hoc arena ring assignment  (§3.7 of MODEL_SPEC)
# ---------------------------------------------------------------------------

def assign_arena_ring(segment_schedules, group_opt_slots, bis_opt_slot, show):
    """
    After breed scheduling, pick the ring whose last segment ends earliest
    as the arena ring and assign groups + BIS there sequentially.

    group_opt_slots : {gid: absolute_start_slot} from the optimizer (C8-constrained)
    bis_opt_slot    : absolute BIS start from the optimizer (C9/C11-constrained)

    Group times are pushed forward if the ring isn't free yet, but never
    pulled earlier than the optimizer's values.  BIS follows the last group
    but is also never earlier than the optimizer's tau_bis.
    """
    ring_last: Dict[str, int] = {rid: 0 for rid in show.rings}
    for ss in segment_schedules:
        ring_last[ss.ring_id] = max(ring_last[ss.ring_id], ss.end_slot)

    arena_ring = min(ring_last, key=ring_last.get)
    arena_free = ring_last[arena_ring]

    sorted_groups = sorted(group_opt_slots.items(), key=lambda x: x[1])
    group_schedules = []
    current = arena_free
    for gid, opt_start in sorted_groups:
        start = max(opt_start, current)
        grp   = show.groups[gid]
        end   = start + grp.judging_duration_slots
        group_schedules.append(GroupSchedule(
            group_id=gid,
            judge_id=grp.judge_id,
            ring_id=arena_ring,
            start_slot=start,
            end_slot=end,
            n_breeds=len(grp.breed_ids) if hasattr(grp, 'breed_ids') else 0,
        ))
        current = end

    bis_start = max(bis_opt_slot, current)
    return group_schedules, bis_start
