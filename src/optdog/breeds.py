"""AKC breed catalog: resolves the many spellings used in judging programs to Breeds."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from optdog.domain import Breed, Examination, Group, RingSize

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "akc_breeds.csv"

_GROUPS = {g.value: g for g in Group}


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def name_key(name: str) -> str:
    """Normalize a breed name for lookup: case, punctuation, plurals, spacing."""
    s = name.lower().replace("’", "'").replace("&", " and ").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return "".join(_singular(w) for w in s.split())


class BreedCatalog:
    def __init__(self, breeds: list[Breed], aliases: dict[str, Breed]):
        self.breeds = breeds
        self._by_key: dict[str, Breed] = {}
        for b in breeds:
            self._add(name_key(b.name), b)
        for alias, b in aliases.items():
            self._add(name_key(alias), b)

    def _add(self, key: str, breed: Breed) -> None:
        existing = self._by_key.get(key)
        if existing is not None and existing != breed:
            raise ValueError(f"breed key collision {key!r}: {existing.name} vs {breed.name}")
        self._by_key[key] = breed

    def get(self, name: str) -> Breed | None:
        return self._by_key.get(name_key(name))

    def __getitem__(self, name: str) -> Breed:
        b = self.get(name)
        if b is None:
            raise KeyError(name)
        return b

    @classmethod
    def load(cls, path: str | Path = CATALOG_PATH) -> "BreedCatalog":
        breeds: list[Breed] = []
        aliases: dict[str, Breed] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                b = Breed(
                    name=row["name"],
                    group=_GROUPS[row["group"]] if row["group"] else None,
                    examination=Examination(row["examination"]),
                    variety_of=row["variety_of"] or None,
                    ring_override=RingSize[row["min_ring"]] if row.get("min_ring") else None,
                )
                breeds.append(b)
                for a in filter(None, (row["aliases"] or "").split("|")):
                    aliases[a] = b
        return cls(breeds, aliases)


@lru_cache(maxsize=1)
def default_catalog() -> BreedCatalog:
    return BreedCatalog.load()
