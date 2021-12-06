import pytest

from optdog.domain.breed import Breed
from optdog.domain.conformation_platform import ConformationPlatform
from optdog.domain.group import Group
from optdog.solvers.mz.mz_ring_scheduling import MZRingScheduling


def test_solving():
    problem = MZRingScheduling(
        [
            Breed('Boston Terrier', ConformationPlatform.Table, Group.NonSporting),
            Breed('German Shepherd', ConformationPlatform.Ground, Group.Herding),
            Breed('Basset Hound', ConformationPlatform.Ramp, Group.Hound),
        ]
    )

    result = problem.solve()

    assert result.answer().name == 'Basset Hound'

