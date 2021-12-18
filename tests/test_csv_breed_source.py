import pytest

from optdog.csv.csv_breed_data_source import CSVBreedDataSource


def test_reading_breeds_from_csv():
    source = CSVBreedDataSource('breeds.csv')
    assert 216 == sum(1 for _ in source.breeds())
