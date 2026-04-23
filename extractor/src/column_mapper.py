"""Semantic column header -> canonical field mapper.

PDF-agnostic: relies only on the synonym dictionary in config plus a small
fuzzy-match tolerance (Levenshtein). No per-PDF profiles.
"""

from __future__ import annotations

import re
from typing import List, Optional

from rapidfuzz.distance import Levenshtein

from .interfaces import ColumnMapping


_WS_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[:.]+$")
# Minimum length for a header to be eligible for fuzzy matching.
# Short tokens like "qty", "pos", "no" must match exactly to avoid false
# positives (e.g. "no" <-> "nr" is distance 2).
_FUZZY_MIN_LEN = 4


def _normalize(header: Optional[str]) -> str:
    """Normalize a raw header string for comparison.

    - None/empty -> ""
    - U+FFFD (replacement char) -> "."
    - newlines -> spaces
    - collapse whitespace
    - lowercase, strip
    - strip trailing ':' and '.'
    """
    if header is None:
        return ""
    s = str(header)
    # Replace the Unicode replacement character with '.' (per spec).
    s = s.replace("�", ".")
    # Newlines (and other vertical whitespace) -> space.
    s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # Lowercase + trim.
    s = s.lower().strip()
    # Collapse any run of whitespace to a single space.
    s = _WS_RE.sub(" ", s)
    # Strip trailing ':' and '.' (repeated, e.g. "Qty.:").
    s = _TRAILING_PUNCT_RE.sub("", s).strip()
    return s


def _build_synonym_index(synonyms_cfg: dict) -> List[tuple]:
    """Flatten the synonyms config into an ordered list of
    (canonical_field, normalized_synonym, original_synonym) tuples.

    Preserves dict iteration order (Python 3.7+) so that ties resolve to
    the first canonical field declared in the config.
    """
    index: List[tuple] = []
    for canonical_field, syn_list in synonyms_cfg.items():
        if not syn_list:
            continue
        for syn in syn_list:
            if syn is None:
                continue
            norm = _normalize(syn)
            if not norm:
                continue
            index.append((canonical_field, norm, str(syn)))
    return index


def map_columns(headers: List[Optional[str]], config: dict) -> List[ColumnMapping]:
    """Map each raw column header to a canonical field.

    See module docstring and interfaces.ColumnMapping for semantics.
    """
    cm_cfg = (config or {}).get("column_mapping", {}) or {}
    fuzzy_max_distance = int(cm_cfg.get("fuzzy_max_distance", 2))
    synonyms_cfg = cm_cfg.get("synonyms", {}) or {}

    syn_index = _build_synonym_index(synonyms_cfg)

    results: List[ColumnMapping] = []
    for idx, raw in enumerate(headers):
        raw_str = "" if raw is None else str(raw)
        norm = _normalize(raw)

        # Empty after normalization -> non-match.
        if not norm:
            results.append(
                ColumnMapping(
                    column_index=idx,
                    raw_header=raw_str,
                    normalized_header="",
                    canonical_field=None,
                    match_method="none",
                    match_distance=0,
                    matched_synonym=None,
                )
            )
            continue

        # --- Exact match pass (first hit wins; preserves dict order). ---
        exact_hit = None
        for canonical_field, syn_norm, syn_orig in syn_index:
            if norm == syn_norm:
                exact_hit = (canonical_field, syn_orig)
                break

        if exact_hit is not None:
            canonical_field, syn_orig = exact_hit
            results.append(
                ColumnMapping(
                    column_index=idx,
                    raw_header=raw_str,
                    normalized_header=norm,
                    canonical_field=canonical_field,
                    match_method="exact",
                    match_distance=0,
                    matched_synonym=syn_orig,
                )
            )
            continue

        # --- Fuzzy match pass (only for headers >= 4 chars). ---
        if len(norm) >= _FUZZY_MIN_LEN:
            best = None  # (distance, canonical_field, syn_orig, order_index)
            for order_idx, (canonical_field, syn_norm, syn_orig) in enumerate(syn_index):
                d = Levenshtein.distance(norm, syn_norm)
                if d <= fuzzy_max_distance:
                    if best is None or d < best[0]:
                        best = (d, canonical_field, syn_orig, order_idx)
                    # Ties: keep earlier dict-order entry (already preserved
                    # because we do not overwrite on equal distance).

            if best is not None:
                d, canonical_field, syn_orig, _ = best
                results.append(
                    ColumnMapping(
                        column_index=idx,
                        raw_header=raw_str,
                        normalized_header=norm,
                        canonical_field=canonical_field,
                        match_method="fuzzy",
                        match_distance=d,
                        matched_synonym=syn_orig,
                    )
                )
                continue

        # --- No match. ---
        results.append(
            ColumnMapping(
                column_index=idx,
                raw_header=raw_str,
                normalized_header=norm,
                canonical_field=None,
                match_method="none",
                match_distance=0,
                matched_synonym=None,
            )
        )

    return results
