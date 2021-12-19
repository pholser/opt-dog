from optdog.domain.event_type import EventType


class ShowDay:
    def __init__(self):
        self.events = []

    def add_event(self, event):
        self.events.append(event)

    def breeds(self):
        return [ev.breed for ev in self.breed_judging_events()]

    def confirmation_platform_for_breed(self, breed):
        breed_event = next(ev for ev in self.breed_judging_events() if ev.breed == breed)
        return breed_event.conformation_platform

    def breed_judging_events(self):
        return list(filter(
            lambda ev: ev.type == EventType.BreedJudging,
            self.events
        ))

    def group_breed_judging_events(self):
        return list(filter(
            lambda ev: ev.type == EventType.GroupBreedJudging,
            self.events
        ))

    def best_in_show_breed_judging_event(self):
        return list(filter(
            lambda ev: ev.type == EventType.BestInShowBreedJudging,
            self.events
        ))[0]

    def judges(self):
        return list(set(map(lambda ev: ev.judge, self.events)))
