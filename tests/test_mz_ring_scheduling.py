import pytest

from optdog.domain.breed import Breed
from optdog.domain.breed_judging_event import BreedJudgingEvent
from optdog.domain.conformation_platform import ConformationPlatform
from optdog.domain.group import Group
from optdog.domain.judge import Judge
from optdog.domain.show_day import ShowDay
from optdog.solvers.mz.mz_ring_scheduling import MZRingScheduling


def test_solving():
    bostons = Breed('Boston Terrier', [ConformationPlatform.Table], Group.NonSporting)
    german_shepherds = Breed('German Shepherd', [ConformationPlatform.Ground], Group.Herding)
    bassets = Breed('Basset Hound', [ConformationPlatform.Ramp], Group.Hound)
    mary = Judge('Mary Wisenheimer', [german_shepherds, bassets])

    show_day = ShowDay()
    show_day.add_event(
        BreedJudgingEvent(
            'Boston Terrier Breed Judging',
            Judge('Joseph McDougal', [bostons]),
            bostons,
            ConformationPlatform.Table
        )
    )
    show_day.add_event(
        BreedJudgingEvent(
            'German Shepherd Breed Judging',
            mary,
            german_shepherds,
            ConformationPlatform.Ground
        )
    )
    show_day.add_event(
        BreedJudgingEvent(
            'Basset Hound Breed Judging',
            mary,
            bassets,
            ConformationPlatform.Ramp
        )
    )

    problem = MZRingScheduling(show_day)

    result = problem.solve()

    assert result.breed() == show_day.breeds()[0]
    assert result.event() == show_day.events[0]
    assert result.judge() == show_day.judges()[0]
