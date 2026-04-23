"""Signal 2: column-header fingerprint.

The heaviest-weighted signal in the classifier. Scans every table extracted
from the page by pdfplumber, tries the first two rows as candidate headers,
normalizes each cell, and matches against a synonyms dict. A unique category
counts once even if multiple headers map to it. The table with the most
matched categories wins; the best header row within that table is picked.

Config lives under ctx.config["signals"]["column_header"]. See config.yaml.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz.distance import Levenshtein

from ..interfaces import PageContext, SignalResult


_WS_RE = re.compile(r"\s+")
_TRAIL_PUNCT_RE = re.compile(r"[:\.\s]+$")


def _normalize(cell: Any) -> Optional[str]:
    """Normalize a raw header cell.

    Returns None for empty/None input so callers can cheaply skip it.
    Steps: stringify, replace U+FFFD with ".", replace newlines/tabs with
    spaces, collapse whitespace, lowercase, strip, drop trailing ':' and '.'.
    """
    if cell is None:
        return None
    s = str(cell)
    if not s:
        return None
    # U+FFFD is the unicode replacement char pdfplumber sometimes emits.
    s = s.replace("�", ".")
    # Newlines/tabs to spaces so multi-line headers collapse.
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = _WS_RE.sub(" ", s).strip().lower()
    # Remove trailing punctuation that shows up on some PDFs ("Device tag:").
    s = _TRAIL_PUNCT_RE.sub("", s).strip()
    if not s:
        return None
    return s


def _match_header(norm: str, synonyms: Dict[str, List[str]], max_dist: int) -> Optional[str]:
    """Return the category name matched by `norm`, or None.

    Exact match wins outright. Otherwise fuzzy-match (Levenshtein <= max_dist)
    but only when BOTH strings are >=4 chars so "qty" vs "nr" don't collide.
    """
    # First pass: exact match across all categories.
    for category, variants in synonyms.items():
        for variant in variants:
            vnorm = _normalize(variant)
            if vnorm is not None and vnorm == norm:
                return category
    # Second pass: fuzzy match, but only for strings long enough to be safe.
    if len(norm) < 4 or max_dist <= 0:
        return None
    best_cat: Optional[str] = None
    best_dist: int = max_dist + 1
    for category, variants in synonyms.items():
        for variant in variants:
            vnorm = _normalize(variant)
            if vnorm is None or len(vnorm) < 4:
                continue
            # Skip pairs that can't possibly be within max_dist.
            if abs(len(vnorm) - len(norm)) > max_dist:
                continue
            d = Levenshtein.distance(norm, vnorm)
            if d <= max_dist and d < best_dist:
                best_dist = d
                best_cat = category
                if d == 0:
                    return best_cat
    return best_cat


def _score_header_row(
    row: List[Any],
    synonyms: Dict[str, List[str]],
    max_dist: int,
) -> Tuple[Dict[str, str], List[str]]:
    """Scan a single candidate header row.

    Returns (category -> first-header-that-matched, original-headers-kept).
    """
    matched: Dict[str, str] = {}
    originals: List[str] = []
    for cell in row:
        norm = _normalize(cell)
        if norm is None:
            continue
        cat = _match_header(norm, synonyms, max_dist)
        if cat is None:
            continue
        originals.append(str(cell))
        if cat not in matched:
            matched[cat] = str(cell)
    return matched, originals


def compute(ctx: PageContext) -> SignalResult:
    cfg = ctx.config.get("signals", {}).get("column_header", {})

    if not cfg.get("enabled", True):
        return SignalResult(
            name="column_header",
            score=0.0,
            raw_value=0,
            details={"skipped": True},
            matched_table_index=None,
        )

    tables = ctx.tables or []
    if not tables:
        return SignalResult(
            name="column_header",
            score=0.0,
            raw_value=0,
            details={
                "matched_categories": [],
                "matched_headers": [],
                "best_table_index": None,
                "best_header_row_index": None,
                "qualification": "none",
                "all_tables_scanned": 0,
                "best_table_dimensions": None,
            },
            matched_table_index=None,
        )

    synonyms: Dict[str, List[str]] = cfg.get("synonyms", {}) or {}
    max_dist = int(cfg.get("fuzzy_max_distance", 2))
    strong_threshold = int(cfg.get("strong_threshold", 4))
    weak_threshold = int(cfg.get("weak_threshold", 3))
    strong_weight = float(cfg.get("strong_weight", 10.0))
    weak_weight = float(cfg.get("weak_weight", 5.0))

    best_table_idx: Optional[int] = None
    best_row_idx: Optional[int] = None
    best_count: int = 0
    best_matched: Dict[str, str] = {}
    best_originals: List[str] = []
    best_dims: Optional[List[int]] = None

    for t_idx, table in enumerate(tables):
        if not table:
            continue
        # Try the first two rows as header candidates - some PDFs have a
        # spanning title on row 0 and the real header on row 1.
        candidate_rows = table[:2]
        local_best_count = -1
        local_best_row_idx = 0
        local_best_matched: Dict[str, str] = {}
        local_best_originals: List[str] = []
        for r_idx, row in enumerate(candidate_rows):
            if not row:
                continue
            matched, originals = _score_header_row(row, synonyms, max_dist)
            if len(matched) > local_best_count:
                local_best_count = len(matched)
                local_best_row_idx = r_idx
                local_best_matched = matched
                local_best_originals = originals

        if local_best_count > best_count:
            best_count = local_best_count
            best_table_idx = t_idx
            best_row_idx = local_best_row_idx
            best_matched = local_best_matched
            best_originals = local_best_originals
            best_dims = [len(table), max((len(r) for r in table), default=0)]

    if best_count >= strong_threshold:
        score = strong_weight
        qualification = "strong"
    elif best_count >= weak_threshold:
        score = weak_weight
        qualification = "weak"
    else:
        score = 0.0
        qualification = "none"

    matched_table_index = best_table_idx if best_count > 0 else None

    details: Dict[str, Any] = {
        "matched_categories": sorted(best_matched.keys()),
        "matched_headers": best_originals,
        "best_table_index": best_table_idx,
        "best_header_row_index": best_row_idx,
        "qualification": qualification,
        "all_tables_scanned": len(tables),
        "best_table_dimensions": best_dims,
    }

    return SignalResult(
        name="column_header",
        score=score,
        raw_value=best_count,
        details=details,
        matched_table_index=matched_table_index,
    )
