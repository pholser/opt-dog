from abc import ABC


class RingSchedulingSolution(ABC):
    def __init__(self, breeds, events, judges):
        self.breeds = breeds
        self.events = events
        self.judges = judges
