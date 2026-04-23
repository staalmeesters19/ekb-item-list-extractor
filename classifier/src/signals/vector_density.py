"""Signal 4: vector density ("page quietness").

Schematic pages contain hundreds/thousands of vector primitives (wires, symbols,
boxes). Item-list pages are visually "quiet" — only the table border grid and
some text. Counting raw vector primitives on the page lets us discriminate
between the two without depending on any specific CAD software.

This signal is confirming, not decisive: weight is low (e.g. 2.0) compared to
the column-header signal (e.g. 10.0).
"""

from ..interfaces import PageContext, SignalResult


def _safe_len(attr) -> int:
    """Return len() of a pdfplumber attribute, or 0 if missing/None/raising."""
    try:
        if attr is None:
            return 0
        return len(attr)
    except Exception:
        return 0


def compute(ctx: PageContext) -> SignalResult:
    cfg = ctx.config.get("signals", {}).get("vector_density", {})

    if not cfg.get("enabled", False):
        return SignalResult(
            name="vector_density",
            score=0.0,
            raw_value=0,
            details={"skipped": True},
        )

    weight = float(cfg.get("weight", 2.0))
    quiet_threshold = int(cfg.get("quiet_threshold", 400))
    noisy_threshold = int(cfg.get("noisy_threshold", 2000))
    noisy_penalty = float(cfg.get("noisy_penalty", -1.0))

    # Count vector primitives. Each attribute access on a pdfplumber page can
    # in theory raise (malformed content streams), so wrap defensively.
    try:
        lines = _safe_len(getattr(ctx.page, "lines", None))
    except Exception:
        lines = 0
    try:
        curves = _safe_len(getattr(ctx.page, "curves", None))
    except Exception:
        curves = 0
    try:
        rects = _safe_len(getattr(ctx.page, "rects", None))
    except Exception:
        rects = 0

    total_vectors = lines + curves + rects

    try:
        page_width = float(getattr(ctx.page, "width", 0.0) or 0.0)
    except Exception:
        page_width = 0.0
    try:
        page_height = float(getattr(ctx.page, "height", 0.0) or 0.0)
    except Exception:
        page_height = 0.0

    # Thresholds in config are calibrated on raw counts (no area normalisation).
    if total_vectors <= quiet_threshold:
        score = weight
        qualification = "quiet"
    elif total_vectors >= noisy_threshold:
        score = noisy_penalty
        qualification = "noisy"
    else:
        score = 0.0
        qualification = "neutral"

    return SignalResult(
        name="vector_density",
        score=float(score),
        raw_value=int(total_vectors),
        details={
            "total_vectors": int(total_vectors),
            "lines": int(lines),
            "curves": int(curves),
            "rects": int(rects),
            "page_width": page_width,
            "page_height": page_height,
            "qualification": qualification,
        },
    )
