import pytest

from optdog.domain.akc_status import AkcStatus
from optdog.domain.breed import Breed
from optdog.domain.breed_judging_event import BreedJudgingEvent
from optdog.domain.conformation_platform import ConformationPlatform
from optdog.domain.dog import Dog
from optdog.domain.exhibitor import Exhibitor
from optdog.domain.group import Group
from optdog.domain.judge import Judge
from optdog.domain.show_day import ShowDay
from optdog.solvers.mz.mz_ring_scheduling import MZRingScheduling


def test_solving():
    bostons = Breed('Boston Terrier', [ConformationPlatform.Table], Group.NonSporting)
    german_shepherds = Breed('German Shepherd', [ConformationPlatform.Ground], Group.Herding)
    bassets = Breed('Basset Hound', [ConformationPlatform.Ramp], Group.Hound)

    judge_mary = Judge('Mary Wisenheimer', [german_shepherds, bassets])
    judge_joseph = Judge('Joseph McDougal', [bostons])

    show_day = ShowDay(1)

    bostons_breed_event = BreedJudgingEvent(
        'Boston Terrier Breed Judging',
        judge_joseph,
        bostons,
        ConformationPlatform.Table
    )
    show_day.add_event(bostons_breed_event)
    german_shepherd_breed_event = BreedJudgingEvent(
        'German Shepherd Breed Judging',
        judge_mary,
        german_shepherds,
        ConformationPlatform.Ground
    )
    show_day.add_event(german_shepherd_breed_event)
    basset_hound_breed_event = BreedJudgingEvent(
        'Basset Hound Breed Judging',
        judge_mary,
        bassets,
        ConformationPlatform.Ramp
    )
    show_day.add_event(basset_hound_breed_event)

    # TODO: dogs, exhibitors, events ...
    boston_1 = Dog('Jester', bostons, AkcStatus.Class, False)
    boston_2 = Dog('Michael', bostons, AkcStatus.Champion, False)
    boston_3 = Dog('Nicky', bostons, AkcStatus.Champion, True)
    boston_4 = Dog('Boomer', bostons, AkcStatus.Champion, False)

    german_shepherd_1 = Dog('Rex', german_shepherds, AkcStatus.Class, False)
    german_shepherd_2 = Dog('Fifi', german_shepherds, AkcStatus.Champion, True)
    german_shepherd_3 = Dog('Tiger', german_shepherds, AkcStatus.Champion, False)

    basset_1 = Dog('Flash', bassets, AkcStatus.Champion, True)
    basset_2 = Dog('Junior', bassets, AkcStatus.Class, False)
    basset_3 = Dog('Ralph', bassets, AkcStatus.Champion, False)
    basset_4 = Dog('Daisy', bassets, AkcStatus.Class, False)
    basset_5 = Dog('Annie', bassets, AkcStatus.Champion, False)

    exhibitor_a = Exhibitor('Exhibitor A', False)
    exhibitor_b = Exhibitor('Exhibitor B', False)
    exhibitor_c = Exhibitor('Exhibitor C', True)
    exhibitor_d = Exhibitor('Exhibitor D', False)
    exhibitor_e = Exhibitor('Exhibitor E', False)
    exhibitor_f = Exhibitor('Exhibitor F', True)
    exhibitor_g = Exhibitor('Exhibitor G', False)

    bostons_breed_event.enter(boston_1, exhibitor_a)
    bostons_breed_event.enter(boston_2, exhibitor_b)
    bostons_breed_event.enter(boston_3, exhibitor_c)
    bostons_breed_event.enter(boston_4, exhibitor_d)

    german_shepherd_breed_event.enter(german_shepherd_1, exhibitor_e)
    german_shepherd_breed_event.enter(german_shepherd_2, exhibitor_a)
    german_shepherd_breed_event.enter(german_shepherd_3, exhibitor_f)

    basset_hound_breed_event.enter(basset_1, exhibitor_g)
    basset_hound_breed_event.enter(basset_2, exhibitor_b)
    basset_hound_breed_event.enter(basset_3, exhibitor_c)
    basset_hound_breed_event.enter(basset_4, exhibitor_d)
    basset_hound_breed_event.enter(basset_5, exhibitor_f)

    problem = MZRingScheduling(show_day)

    result = problem.solve()

    assert result.report() == []
