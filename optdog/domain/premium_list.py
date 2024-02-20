from optdog.domain.event import Event
from optdog.domain.event_type import EventType\



class PremiumList:
    def __init__(self):
        self.events = dict()
        self.events['breed_group'] = dict()
        self.events['breed'] = dict()

    def add_best_in_show_event(self, judge):
        self.events['best_in_show'] = Event(
            'best_in_show',
            EventType.BestInShowBreedJudging,
            judge
        )

    def add_breed_group_event(self, group, judge):
        self.events['breed_group'][group] = Event(
            str(group),
            EventType.GroupBreedJudging,
            judge
        )

    def add_breed_event(self, breed, judge):
        self.events['breed'][breed.name] = Event(
            breed.name,
            EventType.BreedJudging,
            judge
        )
