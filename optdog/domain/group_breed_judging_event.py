from optdog.domain.event import Event
from optdog.domain.event_type import EventType


class GroupBreedJudgingEvent(Event):
    def __init__(self, name, judge, group):
        super().__init__(name, EventType.GroupBreedJudging, judge)
        self.group = group
        assert judge.may_judge_group(group)
