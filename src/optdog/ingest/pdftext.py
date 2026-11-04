"""Column-aware text extraction for two-column judging-program PDFs.

Judging programs are typically printed in two columns. Rather than assume the
gutter is at the page midpoint (it is not always), we find, per page, the
vertical strip in the middle band of the page crossed by the fewest words.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class Column:
    page: int  # 1-based
    side: str  # "L" or "R"
    lines: list[str]


def _find_gutter(page) -> float:
    x0, _, x1, _ = page.bbox
    width = x1 - x0
    words = page.extract_words()
    lo, hi = x0 + 0.3 * width, x0 + 0.7 * width
    best_x, best_cost = (x0 + x1) / 2, None
    x = lo
    while x <= hi:
        crossing = sum(1 for w in words if w["x0"] < x < w["x1"])
        # prefer fewer crossings, then closeness to the midpoint
        cost = (crossing, abs(x - (x0 + x1) / 2))
        if best_cost is None or cost < best_cost:
            best_x, best_cost = x, cost
        x += 1.0
    return best_x


def extract_columns(pdf_path: str | Path) -> list[Column]:
    """Split each page at its detected gutter and extract each half's lines."""
    cols: list[Column] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            x0, top, x1, bottom = page.bbox
            gutter = _find_gutter(page)
            for side, box in (("L", (x0, top, gutter, bottom)), ("R", (gutter, top, x1, bottom))):
                text = page.crop(box).extract_text() or ""
                cols.append(Column(i, side, [ln.rstrip() for ln in text.splitlines()]))
    return cols


def dump(pdf_path: str | Path) -> str:
    out = []
    for c in extract_columns(pdf_path):
        out.append(f"=== PAGE {c.page}{c.side} ===")
        out.extend(c.lines)
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    print(dump(sys.argv[1]))
