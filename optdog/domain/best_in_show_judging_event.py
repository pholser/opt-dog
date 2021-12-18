from optdog.domain.event import Event
from optdog.domain.event_type import EventType


class BestInShowJudgingEvent(Event):
    def __init__(self, name, judge):
        super().__init__(name, EventType.BestInShowBreedJudging, judge)
