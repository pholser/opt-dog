from abc import ABC, abstractmethod


class RingScheduling(ABC):
    def __init__(self, breeds):
        self.breeds = breeds

    @abstractmethod
    def solve(self):
        pass
