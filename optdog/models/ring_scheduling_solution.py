from abc import ABC, abstractmethod


class RingSchedulingSolution(ABC):
    def __init__(self, breeds):
        self.breeds = breeds
