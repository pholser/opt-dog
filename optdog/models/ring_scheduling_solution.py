from abc import ABC, abstractmethod


class RingSchedulingSolution(ABC):
    def __init__(self, breeds, events, judges, number_of_rings):
        self.breeds = breeds
        self.events = events
        self.judges = judges
        self.number_of_rings = number_of_rings

    @abstractmethod
    def py_index_of(self, solver_index):
        pass
