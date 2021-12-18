from optdog.domain.breed import Breed
from optdog.domain.breed_data_source import BreedDataSource
from optdog.domain.group import Group
import csv


class CSVBreedDataSource(BreedDataSource):
    def __init__(self, csv_file_path):
        self.backing_store = []
        with open(csv_file_path, newline='') as csv_in:
            csv_reader = csv.DictReader(csv_in)
            for row in csv_reader:
                self.backing_store.append(
                    Breed(
                        row['Name'],
                        row['ConformationPlatforms'].split('|'),
                        Group[row['Group']]
                    )
                )

    def breeds(self):
        return iter(self.backing_store)
