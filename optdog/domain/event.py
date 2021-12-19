from abc import ABC
from optdog.domain.event_entry import EventEntry


class Event(ABC):
    def __init__(self, name, type, judge):
        self.name = name
        self.type = type
        self.judge = judge
        self.entries = []

    def enter(self, dog, exhibitor):
        self.entries.append(EventEntry(self, dog, exhibitor))
