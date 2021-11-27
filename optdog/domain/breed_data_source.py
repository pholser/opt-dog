from abc import ABC, abstractmethod


class BreedDataSource(ABC):
    @abstractmethod
    def breeds(self):
        pass
