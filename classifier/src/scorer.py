"""Scorer: combines per-page signals into a PageScore, and applies
the post-pass continuity bonus."""

from typing import List, Dict
from .interfaces import PageContext, PageScore, SignalResult
from .signals import title, column_header, row_count, vector_density, continuity


# The 4 per-page signals in execution order:
PER_PAGE_SIGNALS = [
    ("title", title.compute),
    ("column_header", column_header.compute),
    ("row_count", row_count.compute),
    ("vector_density", vector_density.compute),
]


def score_page(ctx: PageContext) -> PageScore:
    """Run all per-page signals on one page and return a PageScore."""
    signals: Dict[str, SignalResult] = {}
    for name, fn in PER_PAGE_SIGNALS:
        try:
            signals[name] = fn(ctx)
        except Exception as e:
            # Fail soft: a broken signal shouldn't kill the whole page.
            signals[name] = SignalResult(
                name=name,
                score=0.0,
                raw_value=None,
                details={"error": repr(e)},
            )

    total = sum(sr.score for sr in signals.values())

    # Authoritative header info comes from the column_header signal.
    col_sig = signals.get("column_header")
    best_table_idx = col_sig.matched_table_index if col_sig else None
    best_headers: List[str] = []
    if col_sig and col_sig.details:
        raw_headers = col_sig.details.get("matched_headers", [])
        if isinstance(raw_headers, list):
            best_headers = [h for h in raw_headers if isinstance(h, str) and h.strip()]

    # Row count from row_count signal (raw_value == max_rows).
    rc_sig = signals.get("row_count")
    row_count_val = 0
    if rc_sig is not None and isinstance(rc_sig.raw_value, int):
        row_count_val = rc_sig.raw_value

    return PageScore(
        page_number=ctx.page_number,
        total_score=total,
        signals=signals,
        best_table_index=best_table_idx,
        best_table_headers=best_headers,
        row_count=row_count_val,
    )


def apply_continuity(scores: List[PageScore], config: dict) -> List[PageScore]:
    """Pass 2: apply continuity bonuses based on pass-1 scores.

    Mutates the PageScore objects in place (adds a 'continuity' entry to
    signals and increments total_score) and also returns the list for
    convenient chaining.
    """
    bonuses = continuity.compute_continuity(scores, config)
    for s in scores:
        bonus = bonuses.get(s.page_number)
        if bonus is None:
            continue
        s.signals["continuity"] = bonus
        s.total_score += bonus.score
    return scores
