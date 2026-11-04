"""Read AKC's official breed-classification pages saved under data/raw/akc/:

- akc-breeds-by-group.html     the seven variety groups
- akc-miscellaneous-class.html the Miscellaneous Class
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from optdog.domain import Group

_AKC = Path(__file__).resolve().parents[2] / "data" / "raw" / "akc"
PAGE = _AKC / "akc-breeds-by-group.html"
MISC_PAGE = _AKC / "akc-miscellaneous-class.html"

_HEADINGS = {
    "Sporting": Group.SPORTING,
    "Hound": Group.HOUND,
    "Working": Group.WORKING,
    "Terrier": Group.TERRIER,
    "Toy": Group.TOY,
    "Non-Sporting": Group.NON_SPORTING,
    "Herding": Group.HERDING,
}


def official_groups(path: Path = PAGE) -> dict[Group, list[tuple[str, bool]]]:
    """{group: [(breed name, has_varieties)]} as listed on the AKC page."""
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"(?s)<(script|style).*?</\1>", "", t)
    t = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|td)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]
    out: dict[Group, list[tuple[str, bool]]] = {}
    group: Group | None = None
    for ln in lines:
        m = re.match(r"^(Sporting|Hound|Working|Terrier|Toy|Non-Sporting|Herding) Group\s*[–-]\s*\d+ breeds", ln)
        if m:
            group = _HEADINGS[m.group(1)]
            out[group] = []
            continue
        if group is None:
            continue
        if ln == "Other Fun Facts":
            break
        if ln in ("+",) or ln in _HEADINGS or len(ln) > 60:
            continue  # heading repeat or group description paragraph
        has_var = ln.endswith("**")
        out[group].append((ln.rstrip("* ").strip(), has_var))
    return out


def official_miscellaneous(path: Path = MISC_PAGE) -> list[str]:
    """Breed names on AKC's Miscellaneous Class page (breed cards)."""
    t = path.read_text(encoding="utf-8", errors="replace")
    names = re.findall(r'breed-type-card__title[^>]*>([^<]+)<', t)
    return [html.unescape(n).strip() for n in names]
