from abc import ABC, abstractmethod


class RingScheduling(ABC):
    @abstractmethod
    def solve(self):
        pass
