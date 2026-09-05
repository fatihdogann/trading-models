"""Setup scoring — strictly separated from model validation.

Validation (required conditions) decides whether a setup may trigger at all;
scoring ranks setups that did trigger. A high score can never substitute for
a missing required condition.
"""

from __future__ import annotations

from tme.types import ChecklistItem, ScoreResult


def build_score(
    components: dict[str, tuple[bool, str]],
    weights: dict[str, float],
    required: tuple[str, ...],
) -> ScoreResult:
    """components: name -> (passed, detail). Missing names count as failed."""
    items: list[ChecklistItem] = []
    total = 0.0
    max_total = 0.0
    for name, (passed, detail) in components.items():
        w = float(weights.get(name, 0.0))
        max_total += w
        if passed:
            total += w
        items.append(ChecklistItem(name=name, passed=passed, weight=w,
                                   required=name in required, detail=detail))
    missing = tuple(n for n in required if not components.get(n, (False, ""))[0])
    return ScoreResult(
        total=round(total, 2),
        max_total=round(max_total, 2),
        passed_required=not missing,
        missing_required=missing,
        items=tuple(items),
    )
