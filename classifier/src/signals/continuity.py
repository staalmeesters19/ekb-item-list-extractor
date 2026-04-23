"""Signal 5: continuity (post-pass).

Unlike other signals, this one runs AFTER pass-1 scoring. It awards a bonus
to a page when at least one of its neighbours scored high in pass 1, so that
"tail" or "middle" pages of a multi-page item-list that might score weakly
on their own still get pulled across the threshold.

Contract differs from the standard per-page compute(ctx) signature:

    compute_continuity(scores: List[PageScore], config: dict)
        -> Dict[int, SignalResult]
"""

from typing import Dict, List

from ..interfaces import PageScore, SignalResult


SIGNAL_NAME = "continuity"


def compute_continuity(
    scores: List[PageScore], config: dict
) -> Dict[int, SignalResult]:
    """Compute per-page continuity bonus based on pass-1 neighbour scores.

    Args:
        scores: all pass-1 PageScore results, sorted by page_number (1-indexed).
        config: full config dict; reads config["signals"]["continuity"].

    Returns:
        Dict mapping page_number -> SignalResult with name="continuity".
    """
    cfg = config.get("signals", {}).get(SIGNAL_NAME, {})
    enabled = cfg.get("enabled", True)

    # Disabled: return zero-score entries for every page.
    if not enabled:
        return {
            ps.page_number: SignalResult(
                name=SIGNAL_NAME,
                score=0.0,
                raw_value=0.0,
                details={"skipped": True},
            )
            for ps in scores
        }

    weight = float(cfg.get("weight", 2.0))
    base_threshold = float(cfg.get("base_threshold", 7.0))

    # Index by page_number so we handle non-contiguous page lists correctly.
    by_page: Dict[int, PageScore] = {ps.page_number: ps for ps in scores}

    results: Dict[int, SignalResult] = {}
    for ps in scores:
        n = ps.page_number
        left = by_page.get(n - 1)
        right = by_page.get(n + 1)

        left_score = left.total_score if left is not None else None
        right_score = right.total_score if right is not None else None

        qualifying = 0
        if left_score is not None and left_score >= base_threshold:
            qualifying += 1
        if right_score is not None and right_score >= base_threshold:
            qualifying += 1

        # Cap bonus at `weight` regardless of 1 or 2 qualifying neighbours.
        bonus = weight if qualifying >= 1 else 0.0

        results[n] = SignalResult(
            name=SIGNAL_NAME,
            score=bonus,
            raw_value=bonus,
            details={
                "left_neighbour_score": left_score,
                "right_neighbour_score": right_score,
                "qualifying_neighbours": qualifying,
                "base_threshold": base_threshold,
            },
        )

    return results
