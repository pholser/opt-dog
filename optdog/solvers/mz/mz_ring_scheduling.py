from minizinc import Instance, Model, Solver, Status

from optdog.domain.akc_status import AkcStatus
from optdog.domain.conformation_platform import ConformationPlatform
from optdog.domain.event_type import EventType
from optdog.domain.group import Group
from optdog.models.ring_scheduling import RingScheduling
from optdog.models.ring_scheduling_solution import RingSchedulingSolution


class MZRingScheduling(RingScheduling):
    def __init__(self, show_day):
        model = Model()
        model.add_file('/Users/pholser/opt-dog/mz/breed-rings-build-up.mzn')
        self.instance = Instance(Solver.lookup('gecode'), model)
        self.instance['GROUP'] = Group
        self.instance['CONFORMATION_PLATFORM'] = ConformationPlatform
        self.instance['EVENT_TYPE'] = EventType
        self.instance['AKC_STATUS'] = AkcStatus
        self.breeds = show_day.breeds()
        self.instance['number_of_breeds'] = len(self.breeds)
        self.instance['breed_group'] = [b.group for b in self.breeds]
        self.instance['breed_conformation_platform'] = [
            show_day.confirmation_platform_for_breed(b) for b in self.breeds
        ]
        self.events = show_day.events
        self.instance['number_of_events'] = len(self.events)
        self.instance['event_type'] = [ev.type for ev in self.events]

    def solve(self):
        result = self.instance.solve()
        if result.status.has_solution():
            return MZRingSchedulingSolution(result.solution, self.breeds, self.events)
        return None


class MZRingSchedulingSolution(RingSchedulingSolution):
    def __init__(self, mz_solution, breeds, events):
        super().__init__(breeds, events)
        self.mz_solution = mz_solution

    def breed(self):
        return self.breeds[self.mz_solution.b - 1]

    def event(self):
        return self.events[self.mz_solution.ev - 1]
