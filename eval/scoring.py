"""Numerical accuracy primitives used by every dimension.

Each dimension surfaces a PASS/PARTIAL/FAIL tier (unchanged) *and* a 0.0-1.0
accuracy score derived from the metrics the test already collects. The score
lets a developer rank implementations numerically instead of just counting
passes.

Conventions:
- 1.0 = perfect, 0.0 = worst. SKIPPED / ERROR cells get `accuracy = None` and
  are excluded from the composite.
- A dimension may expose multiple named components in its `accuracy_components`
  dict; the top-level `accuracy` is the aggregate used for ranking.
- Aggregation within a dimension is the arithmetic mean of its components
  unless the dimension has a reason to do otherwise (dim 8 multiplies, so a
  divergent workflow pulls byte-similarity down to 0).
"""

from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher


def clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity |A intersect B| / |A union B|. Both empty -> 1.0."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def ratio_match(a: float, b: float) -> float:
    """Symmetric closeness in [0,1]. 1.0 when equal, 0.0 when one is zero and
    the other is not. Handles the `(0, 0)` case as 1.0."""
    if a == b:
        return 1.0
    denom = max(abs(a), abs(b))
    if denom == 0:
        return 1.0
    return clamp01(1.0 - abs(a - b) / denom)


def text_similarity(a: str, b: str) -> float:
    """difflib SequenceMatcher ratio in [0,1]. Byte-identical → 1.0."""
    if a == b:
        return 1.0
    if not a and not b:
        return 1.0
    return SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def mean(values: Iterable[float]) -> float:
    vs = list(values)
    if not vs:
        return 0.0
    return sum(vs) / len(vs)
