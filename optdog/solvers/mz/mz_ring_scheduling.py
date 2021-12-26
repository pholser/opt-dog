from minizinc import Instance, Model, Solver, Status

from optdog.models.ring_scheduling import RingScheduling
from optdog.models.ring_scheduling_solution import RingSchedulingSolution


class MZRingScheduling(RingScheduling):
    def __init__(self, show_day):
        model = Model()
        model.add_file('/Users/pholser/opt-dog/mz/breed-rings-build-up.mzn')
        self.instance = Instance(Solver.lookup('gecode'), model)

        self.breeds = show_day.breeds()
        self.instance['number_of_breeds'] = len(self.breeds)
        self.instance['breed_group'] = [b.group for b in self.breeds]
        self.instance['breed_conformation_platform'] = [
            show_day.confirmation_platform_for_breed(b) for b in self.breeds
        ]

        self.events = show_day.events
        self.instance['number_of_events'] = len(self.events)
        self.instance['event_type'] = [ev.type for ev in self.events]

        self.judges = show_day.judges()
        self.instance['number_of_judges'] = len(self.judges)

        self.number_of_rings = show_day.number_of_rings
        self.instance['number_of_rings'] = self.number_of_rings

        self.instance['minutes_per_dog'] = 2
        self.instance['max_time'] = 900
        self.instance['lunch_start'] = 181
        self.instance['lunch_end'] = 360

        self.instance['event_judge'] = [
            self.bit(self.events[ev].judge == self.judges[j])
                for ev in range(len(self.events))
                for j in range(len(self.judges))
        ]


        self.dogs = show_day.dogs()
        self.instance['number_of_dogs'] = len(self.dogs)
        self.instance['dog_breed'] = [
            self.solver_index_of(self.breeds.index(d.breed))
                for d in self.dogs
        ]
        self.instance['dog_akc_status'] = [d.akc_status for d in self.dogs]

        self.exhibitors = show_day.exhibitors()
        self.instance['number_of_exhibitors'] = len(self.exhibitors)
        self.instance['exhibitor_registered_for_conflict_minimization'] = [
            self.bit(x.minimize_conflicts) for x in self.exhibitors
        ]
        self.instance['exhibitor_for_dog_in_event'] = [
            self.bit(self.events[ev].exhibitor_for_dog.get(self.dogs[d]) == self.exhibitors[x])
                for ev in range(len(self.events))
                for d in range(len(self.dogs))
                for x in range(len(self.exhibitors))
        ]


    def bit(self, a_boolean):
        return 1 if a_boolean else 0

    def solver_index_of(self, py_index):
        return py_index + 1

    def solve(self):
        result = self.instance.solve()
        if result.status.has_solution():
            return MZRingSchedulingSolution(
                result.solution,
                self.breeds,
                self.events,
                self.judges,
                self.number_of_rings)
        return None


class MZRingSchedulingSolution(RingSchedulingSolution):
    def __init__(self, mz_solution, breeds, events, judges, number_of_rings):
        super().__init__(breeds, events, judges, number_of_rings)
        self.mz_solution = mz_solution

    def report(self):
        lines = []
        for ev in range(len(self.events)):
            for r in range(self.number_of_rings):
                if self.mz_solution.event_held_in_ring[ev][r]:
                    lines.append(
                        "Hold event " + self.events[ev].name
                            + " in ring " + str(self.solver_index_of(r))
                            + " at time "
                            + str(self.mz_solution.event_start_time[ev]))
        return lines

    def py_index_of(self, solver_index):
        return solver_index - 1

    def solver_index_of(self, py_index):
        return py_index + 1

    def breed(self):
        return self.breeds[self.py_index_of(self.mz_solution.b)]

    def event(self):
        return self.events[self.py_index_of(self.mz_solution.ev)]

    def judge(self):
        return self.judges[self.py_index_of(self.mz_solution.j)]
