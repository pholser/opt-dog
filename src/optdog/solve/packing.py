"""Step 3c: order the breeds inside a run of a judge's events so that they pack into full blocks.

A small breed beside one too long to share a block is a short block however the blocks are cut,
and 3a/3b cannot move it. Giving every breed its own place in the model's circuit would let them,
at thousands of arcs per day, each with a constraint per ring. So the order is chosen first, as a
small bin-packing problem per run, and the model then cuts, places and times it as in 3b. The
result is optimal only for this order.

An item stays whole: the varieties of one breed, which humans keep consecutive 82% of the time
(SPEC §8). An item over the block cap has a bin to itself.
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from optdog.domain import Duration, JudgingEvent

Item = list[JudgingEvent]
SHORT_OVER_BLOCK = 1000  # a short block costs more than any number of extra blocks


def items_of(run: list[JudgingEvent]) -> list[Item]:
    """``run``'s events, in order, gathered into the pieces that stay together."""
    def family(e: JudgingEvent) -> str:
        if e.breed is None:
            return e.name
        return e.breed.variety_of or e.breed.name

    items: dict[str, Item] = {}
    for e in run:
        items.setdefault(family(e), []).append(e)
    return list(items.values())


def pack_run(run: list[JudgingEvent], cap: Duration, short_dogs: int, time_limit: float = 2.0) -> list[list[JudgingEvent]]:
    """``run`` as bins of events, fewest short bins first, then fewest bins; fullest bins first."""
    items = items_of(run)
    dogs = [sum(e.entries for e in it) for it in items]
    units = 10  # to a minute
    mins = [Duration.total(e.duration for e in it).units(units) for it in items]
    cap_units = int(cap.minutes * units)
    alone = [i for i, m in enumerate(mins) if m > cap_units]
    free = [i for i in range(len(items)) if i not in alone]
    bins: list[list[int]] = [[i] for i in alone]
    if free:
        md = cp_model.CpModel()
        n = len(free)
        x = {(i, b): md.new_bool_var(f"x_{i}_{b}") for i in free for b in range(n)}
        used = [md.new_bool_var(f"used_{b}") for b in range(n)]
        short = [md.new_bool_var(f"short_{b}") for b in range(n)]
        for i in free:
            md.add_exactly_one(x[i, b] for b in range(n))
        for b in range(n):
            md.add(sum(mins[i] * x[i, b] for i in free) <= cap_units * used[b])
            md.add(sum(dogs[i] * x[i, b] for i in free) + short_dogs * short[b] >= short_dogs * used[b])
            if b:
                md.add_implication(used[b], used[b - 1])
        md.minimize(SHORT_OVER_BLOCK * sum(short) + sum(used))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = 0
        if solver.solve(md) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return [list(run)]  # keep the order we were given
        for b in range(n):
            members = [i for i in free if solver.value(x[i, b])]
            if members:
                bins.append(members)
    bins.sort(key=lambda b: (-sum(dogs[i] for i in b), b[0]))
    return [[e for i in b for e in items[i]] for b in bins]
