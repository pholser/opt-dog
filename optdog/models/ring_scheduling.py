from abc import ABC, abstractmethod


class RingScheduling(ABC):
    @abstractmethod
    def solve(self):
        pass

    @abstractmethod
    def solver_index_of(self, py_index):
        pass
