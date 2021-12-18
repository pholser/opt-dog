from abc import ABC


class Event(ABC):
    def __init__(self, name, type, judge):
        self.name = name
        self.type = type
        self.judge = judge
