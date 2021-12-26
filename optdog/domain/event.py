from abc import ABC


class Event(ABC):
    def __init__(self, name, type, judge):
        self.name = name
        self.type = type
        self.judge = judge
        self.exhibitor_for_dog = dict()

    def enter(self, dog, exhibitor):
        # TODO: check that dog is not already entered?
        assert dog not in self.exhibitor_for_dog
        self.exhibitor_for_dog[dog] = exhibitor
