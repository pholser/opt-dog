from minizinc import Instance, Model, Solver, Status

from optdog.domain.akc_status import AkcStatus
from optdog.domain.conformation_platform import ConformationPlatform
from optdog.domain.event_type import EventType
from optdog.domain.group import Group
from optdog.models.ring_scheduling import RingScheduling
from optdog.models.ring_scheduling_solution import RingSchedulingSolution


class MZRingScheduling(RingScheduling):
    def __init__(self, breeds):
        super().__init__(breeds)
        model = Model()
        model.add_file('/Users/pholser/opt-dog/mz/breed-rings-build-up.mzn')
        self.instance = Instance(Solver.lookup("gecode"), model)
        self.instance['GROUP'] = Group
        self.instance['CONFORMATION_PLATFORM'] = ConformationPlatform
        self.instance['EVENT_TYPE'] = EventType
        self.instance['AKC_STATUS'] = AkcStatus
        self.instance['number_of_breeds'] = len(breeds)
        self.instance['breed_group'] = [b.group for b in breeds]
        self.instance['breed_conformation_platform'] = [
            b.conformation_platform for b in breeds
        ]

    def solve(self):
        result = self.instance.solve()
        if result.status == Status.OPTIMAL_SOLUTION:
            return MZRingSchedulingSolution(result.solution)
        


class MZRingSchedulingSolution(RingSchedulingSolution):
    pass
