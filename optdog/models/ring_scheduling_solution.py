from abc import ABC


class RingSchedulingSolution(ABC):
    def __init__(self, breeds, events):
        self.breeds = breeds
        self.events = events
