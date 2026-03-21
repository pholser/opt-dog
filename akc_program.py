"""
akc_program.py — Phase D: Judging Program Generator
=====================================================
Converts a SolveResult into a formatted AKC judging program (plain text).
"""

import sys
from io import StringIO
from typing import TextIO


def _fmt_time(hhmm: str) -> str:
    h, m = map(int, hhmm.split(':'))
    if h == 0:
        return f"12:{m:02d} AM"
    elif h < 12:
        return f"{h}:{m:02d} AM"
    elif h == 12 and m == 0:
        return "12:00 NOON"
    elif h == 12:
        return f"12:{m:02d} PM"
    else:
        return f"{h - 12}:{m:02d} PM"


def _num_to_word(n) -> str:
    words = [
        "", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
        "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
        "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN", "TWENTY",
        "TWENTY ONE", "TWENTY TWO", "TWENTY THREE", "TWENTY FOUR", "TWENTY FIVE",
        "TWENTY SIX", "TWENTY SEVEN", "TWENTY EIGHT", "TWENTY NINE", "THIRTY",
    ]
    try:
        i = int(n)
        if 1 <= i < len(words):
            return words[i]
    except (ValueError, TypeError):
        pass
    return str(n).upper()


def _entry_line(breed_name, n_class_dogs, n_class_bitches,
                n_specials_dogs, n_specials_bitches, n_nonregular, n_total) -> str:
    """
    Format: COUNT  BREED_NAME  dogs-bitches-(spec_dogs-spec_bitches)[-nr]
    e.g.:   27 Pugs   5-7-(10-4)-1
    """
    counts = f"{n_class_dogs}-{n_class_bitches}-({n_specials_dogs}-{n_specials_bitches})"
    if n_nonregular:
        counts += f"-{n_nonregular}"
    return f"{n_total:4d} {breed_name:<42s} {counts}"


def _build_program(result) -> str:
    show   = result.show
    params = show.params
    out    = StringIO()

    def p(*args, **kwargs):
        print(*args, file=out, **kwargs)

    # ── Cover ─────────────────────────────────────────────────────────────────
    from datetime import date as _date
    show_date = (params.show_date if hasattr(params.show_date, 'strftime')
                 else _date.fromisoformat(str(params.show_date)))
    day_str  = show_date.strftime("%A")
    date_str = show_date.strftime("%B %-d, %Y")

    total_dogs   = sum(b.n_total for b in show.breeds.values())
    total_breeds = len(show.breeds)

    p("=" * 72)
    p("  JUDGING PROGRAM")
    p("  All-Breed Dog Show")
    p(f"  {params.club_name}")
    p("  (American Kennel Club Licensed)")
    p(f"  {params.venue_name}")
    p(f"  {day_str}, {date_str}")
    p(f"  Judging begins: {_fmt_time(params.slot_to_hhmm(params.judging_start_slot))}"
      f"   Lunch window: {_fmt_time(params.slot_to_hhmm(params.lunch_start_slot))}"
      f" – {_fmt_time(params.slot_to_hhmm(params.lunch_end_slot))}")
    p("=" * 72)
    p()
    p(f"There are {total_dogs} dogs entered in this show in "
      f"{total_breeds} breeds or varieties.")
    p()
    p("The number before each Breed (Variety) indicates the number of dogs "
      "entered.")
    p("The numbers following each Breed (Variety) indicate:")
    p("  Regular Class Dogs - Regular Class Bitches - "
      "(Best of Breed Dogs - Best of Breed Bitches) - Non-Regular")
    p()

    # ── Index structures ──────────────────────────────────────────────────────
    seg_map = {s.segment_id: s for s in show.segments}

    # ring_id -> list of SegmentSchedule sorted by start_slot
    ring_segs: dict = {}
    for ss in sorted(result.segments, key=lambda s: s.start_slot):
        ring_segs.setdefault(ss.ring_id, []).append(ss)

    def ring_sort_key(r):
        try:
            return (0, int(r))
        except ValueError:
            return (1, r)

    sorted_rings = sorted(ring_segs.keys(), key=ring_sort_key)

    # ── Programme of judging header ───────────────────────────────────────────
    p(f"PROGRAMME OF JUDGING - {day_str.upper()} - {date_str.upper()}")
    p()

    # Judge segments sorted by start_slot — used for soft-lunch gap detection
    judge_segs_ordered: dict = {}
    for _ss in sorted(result.segments, key=lambda x: x.start_slot):
        judge_segs_ordered.setdefault(_ss.judge_id, []).append(_ss)

    # ── Ring sections ─────────────────────────────────────────────────────────
    for ring_id in sorted_rings:
        segs = ring_segs[ring_id]
        ring_word = _num_to_word(ring_id)

        # Ring total across all its segments
        ring_total = sum(
            show.breeds[bid].n_total
            for ss in segs
            for bid in seg_map[ss.segment_id].breed_ids
        )

        for ss_idx, ss in enumerate(segs):
            judge = show.judges[ss.judge_id]
            seg   = seg_map[ss.segment_id]
            breeds = [show.breeds[bid] for bid in seg.breed_ids]

            # ── Ring / Judge header ──────────────────────────────────────────
            # Print a new header block whenever the judge changes, or for the
            # very first segment.  Same judge continuing in the same ring just
            # gets a blank line + new time stamp (no repeated header).
            prev_judge_id = segs[ss_idx - 1].judge_id if ss_idx > 0 else None
            new_judge     = (ss_idx == 0) or (ss.judge_id != prev_judge_id)

            if new_judge:
                p(f"RING {ring_word}")
                p(f"JUDGE: {judge.judge_name}")

                if ss.judge_id in show.judges_requiring_lunch:
                    p("  (Judge will take a 45 minute luncheon break)")

                # Continuation note if this judge was in a different ring earlier
                earlier_other = [
                    s2 for s2 in result.segments
                    if s2.judge_id == ss.judge_id
                    and s2.start_slot < ss.start_slot
                    and s2.ring_id != ring_id
                ]
                if earlier_other:
                    prev = max(earlier_other, key=lambda s: s.start_slot)
                    p(f"  Continuation of assignment from Ring {prev.ring_id} "
                      f"at {_fmt_time(params.slot_to_hhmm(prev.start_slot))}")

                p()

            # ── Breed entries ────────────────────────────────────────────────
            # Determine the lunch slot for this judge (if any).
            # lunch_slot is the start of the lunch recess.
            # In the solver, lunch always falls *between* segments (the ell[j]
            # variable picks the inter-segment gap that overlaps the lunch window).
            # So we check: if this is a lunch-judge AND this segment starts at or
            # after the lunch slot, print the lunch block *before* the opening
            # time stamp (i.e. between the previous segment's breeds and these).
            lunch_slot = result.lunch_slots.get(ss.judge_id)

            # Only print the lunch header once, on the first segment that starts
            # at or after the lunch recess end.
            # We track this via a per-judge set on the result object (we attach
            # it temporarily; it's harmless since SolveResult is a dataclass).
            if not hasattr(result, '_lunch_printed'):
                result._lunch_printed = set()

            just_printed_lunch = False
            if (lunch_slot is not None
                    and ss.judge_id not in result._lunch_printed
                    and ss.start_slot >= lunch_slot):
                # This segment comes after lunch — print the recess block first
                p(f"  {_fmt_time(params.slot_to_hhmm(lunch_slot))}")
                p("      Lunch")
                p(f"  {_fmt_time(params.slot_to_hhmm(lunch_slot + params.slots(45)))}")
                p()
                result._lunch_printed.add(ss.judge_id)
                just_printed_lunch = True

            cur_slot = ss.start_slot
            # Print opening time stamp unless we just printed the post-lunch
            # return time (which already serves as the time anchor)
            if not just_printed_lunch:
                p(f"  {_fmt_time(params.slot_to_hhmm(cur_slot))}")

            for b in breeds:
                p(f"      {_entry_line(b.breed_name, b.n_class_dogs, b.n_class_bitches, b.n_specials_dogs, b.n_specials_bitches, b.n_nonregular, b.n_total)}")
                cur_slot += b.delta_total_slots

            # "See Ring X" note if judge continues in a different ring
            later_other = [
                s2 for s2 in result.segments
                if s2.judge_id == ss.judge_id
                and s2.start_slot > ss.start_slot
                and s2.ring_id != ring_id
            ]
            if later_other:
                nxt = min(later_other, key=lambda s: s.start_slot)
                p(f"  See Ring {nxt.ring_id} at "
                  f"{_fmt_time(params.slot_to_hhmm(nxt.start_slot))} "
                  f"for balance of assignment.")

            # Soft-lunch note: non-mandatory-break judges whose next segment
            # is separated from this one by a gap that falls in the lunch window.
            if ss.judge_id not in show.judges_requiring_lunch:
                jss_list = judge_segs_ordered.get(ss.judge_id, [])
                for k, jss in enumerate(jss_list):
                    if jss.segment_id == ss.segment_id and k + 1 < len(jss_list):
                        nxt_jss = jss_list[k + 1]
                        gap_start = ss.end_slot
                        gap_end   = nxt_jss.start_slot
                        if (gap_end > gap_start
                                and gap_start < params.lunch_end_slot
                                and gap_end   > params.lunch_start_slot):
                            p("  (Lunch at their discretion)")
                        break

            p()

        p(f"      {ring_total:4d} Total Dogs")
        p()

    # ── Group & BIS ───────────────────────────────────────────────────────────
    p()
    p("=" * 72)
    bis_ring_id = result.groups[0].ring_id if result.groups else "Arena"
    p(f"GROUP JUDGING AND BEST IN SHOW  —  Ring {bis_ring_id}")
    p("=" * 72)
    p()

    for gs in sorted(result.groups, key=lambda g: g.start_slot):
        grp   = show.groups[gs.group_id]
        judge = show.judges[grp.judge_id]
        n     = len(grp.breed_ids)
        p(f"  {_fmt_time(params.slot_to_hhmm(gs.start_slot)):<12s}  "
          f"{grp.group_name:<22s}  {judge.judge_name}  ({n} breeds)")

    bis_judge = show.judges[show.bis_judge_id]
    p(f"  {_fmt_time(params.slot_to_hhmm(result.bis_start_slot)):<12s}  "
      f"{'Best in Show':<22s}  {bis_judge.judge_name}")
    p()

    return out.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_program(result, output_path: str) -> None:
    """Write the judging program to a text file."""
    text = _build_program(result)
    with open(output_path, 'w') as f:
        f.write(text)


def print_program(result, file: TextIO = sys.stdout) -> None:
    """Print the judging program to a file-like object (default: stdout)."""
    file.write(_build_program(result))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    from akc_preprocessing import load_show
    from akc_cpsat import solve_show
    from akc_schedule import SolveParams

    parser = argparse.ArgumentParser(
        description="Solve an AKC show and generate a text judging program."
    )
    parser.add_argument("show_file",   help="Path to show workbook (.xlsx)")
    parser.add_argument("output_file", nargs='?', default=None,
                        help="Output path (.txt). Omit to print to stdout.")
    parser.add_argument("--time-limit", type=float, default=600)
    parser.add_argument("--gap",        type=float, default=0.005)
    parser.add_argument("--solver",     default="cpsat",
                        help="Solver to use: cpsat (default)")
    parser.add_argument("--viz",        default=None, metavar="VIZ_FILE",
                        help="Also write an interactive HTML schedule chart to this path.")
    parser.add_argument("--ring-switch-penalty", type=float, default=0.0,
                        dest="ring_switch_penalty",
                        help="Minutes of BIS time each ring switch costs (default 0 = tiebreaker only)")
    parser.add_argument("--forbid-ring-switches", action="store_true",
                        dest="forbid_ring_switches",
                        help="Hard constraint: no judge may change rings between segments")
    args = parser.parse_args()

    print(f"Loading {args.show_file} ...", file=sys.stderr)
    show   = load_show(args.show_file)
    params = SolveParams(gap=args.gap, time_limit_sec=args.time_limit,
                         solver=args.solver,
                         ring_switch_penalty_min=args.ring_switch_penalty,
                         forbid_ring_switches=args.forbid_ring_switches)

    print("Solving ...", file=sys.stderr)
    result = solve_show(show, params)
    print(result.summary(), file=sys.stderr)

    if result.status.upper() not in ("OPTIMAL", "FEASIBLE"):
        print(f"ERROR: No feasible solution (status={result.status}).", file=sys.stderr)
        sys.exit(1)

    if args.output_file:
        generate_program(result, args.output_file)
        print(f"Written: {args.output_file}", file=sys.stderr)
    else:
        print_program(result)

    if args.viz:
        from akc_viz import generate_chart
        generate_chart(result, args.viz)
        print(f"Chart written: {args.viz}", file=sys.stderr)


if __name__ == "__main__":
    _cli()
