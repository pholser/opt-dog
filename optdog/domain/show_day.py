from optdog.domain.event_type import EventType


class ShowDay:
    def __init__(self, number_of_rings):
        self.events = []
        self.number_of_rings = number_of_rings

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
        initial = list(map(lambda ev: ev.judge, self.events))
        result = sorted(set(initial), key=initial.index)
        return list(result)

    def dogs(self):
        entries = map(lambda ev: ev.exhibitor_for_dog, self.events)
        dogs = [en[0] for ens in entries for en in ens.items()]
        result = sorted(set(dogs), key=dogs.index)
        return list(result)

    def exhibitors(self):
        entries = map(lambda ev: ev.exhibitor_for_dog, self.events)
        exhibitors = [en[1] for ens in entries for en in ens.items()]
        result = sorted(set(exhibitors), key=exhibitors.index)
        return list(result)
