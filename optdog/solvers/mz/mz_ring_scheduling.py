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

    def solve(self):
        result = self.instance.solve()
        if result.status.has_solution():
            return MZRingSchedulingSolution(result.solution, self.breeds)
        return None


class MZRingSchedulingSolution(RingSchedulingSolution):
    def __init__(self, mz_solution, breeds):
        super().__init__(breeds)
        self.mz_solution = mz_solution

    def answer(self):
        return self.breeds[self.mz_solution.x - 1]
