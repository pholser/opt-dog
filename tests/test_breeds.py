"""The breed catalog must agree with AKC's official breeds-by-group page."""

from optdog.akc_groups_page import official_groups, official_miscellaneous
from optdog.breeds import default_catalog, name_key
from optdog.domain import Group


def _official_assignment():
    """name_key -> set of groups AKC lists it under (varieties may span groups)."""
    out: dict[str, set[Group]] = {}
    for group, items in official_groups().items():
        for name, _has_varieties in items:
            if name_key(name) == name_key(f"{group.value} Group"):
                continue
            out.setdefault(name_key(name), set()).add(group)
    for name in official_miscellaneous():
        out.setdefault(name_key(name), set()).add(Group.MISCELLANEOUS)
    return out


def _catalog_key(breed, catalog, official):
    """Key under which AKC lists this breed: its own name, an alias, or its variety parent."""
    if breed.variety_of:
        return name_key(breed.variety_of)
    for key in official:
        if catalog._by_key.get(key) == breed:
            return key
    return name_key(breed.name)


def test_every_grouped_breed_matches_akc():
    cat = default_catalog()
    official = _official_assignment()
    problems = []
    for b in cat.breeds:
        key = _catalog_key(b, cat, official)
        groups = official.get(key)
        if b.group is None:
            if groups:
                problems.append(f"{b.name}: catalog has no group, AKC lists {groups}")
        elif not groups:
            problems.append(f"{b.name}: catalog says {b.group.value}, AKC page does not list it")
        elif b.group not in groups:
            problems.append(f"{b.name}: catalog says {b.group.value}, AKC says {groups}")
    assert not problems, "\n".join(problems)


def test_every_akc_breed_is_in_catalog():
    cat = default_catalog()
    missing = []
    for key, groups in _official_assignment().items():
        if key in cat._by_key:
            continue
        if any(b.variety_of and name_key(b.variety_of) == key for b in cat.breeds):
            continue
        missing.append((key, groups))
    assert not missing, missing


def test_group_sizes_match_akc_headings():
    # headings on the AKC page: Sporting 33, Hound 33, Working 32, Terrier 32, Non-Sporting 20, Herding 33
    official = official_groups()
    sizes = {g: len([n for n, _ in items if name_key(n) != name_key(f"{g.value} Group")]) for g, items in official.items()}
    assert sizes[Group.SPORTING] == 33
    assert sizes[Group.HOUND] == 33
    assert sizes[Group.WORKING] == 32
    assert sizes[Group.TERRIER] == 32
    assert sizes[Group.NON_SPORTING] == 20
    assert sizes[Group.HERDING] == 33


def test_miscellaneous_class_matches_akc():
    cat = default_catalog()
    official = {name_key(n) for n in official_miscellaneous()}
    assert len(official) == 12
    catalog_misc = {name_key(b.name) for b in cat.breeds if b.group is Group.MISCELLANEOUS}
    assert catalog_misc == official


def test_variety_groups_exclude_miscellaneous():
    from optdog.domain import VARIETY_GROUPS

    assert len(VARIETY_GROUPS) == 7
    assert Group.MISCELLANEOUS not in VARIETY_GROUPS
    assert not Group.MISCELLANEOUS.is_variety_group


def test_fss_breeds_have_no_group():
    cat = default_catalog()
    for name in ("Drentsche Patrijshond", "Japanese Terrier", "Kishu Ken", "Shikoku"):
        assert cat[name].group is None
