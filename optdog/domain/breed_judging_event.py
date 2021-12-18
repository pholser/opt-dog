from optdog.domain.event import Event
from optdog.domain.event_type import EventType


class BreedJudgingEvent(Event):
    def __init__(self, name, judge, breed, conformation_platform):
        super().__init__(name, EventType.BreedJudging, judge)
        assert judge.may_judge_breed(breed)
        assert conformation_platform in breed.conformation_platforms
        self.breed = breed
        self.conformation_platform = conformation_platform

