"""Table selector: picks the real item-list data table from a set of
tables extracted from one PDF page.

A drawing page typically contains multiple tables (frame, title block,
actual data). The heuristic here:

    1. Filter on column count against [min_columns, max_columns].
    2. Filter on minimum data-row count.
    3. Optionally require a "quantity-like" header (exact or Levenshtein<=2).
    4. Among survivors, pick the shortest (fewest rows);
       tie-break on fewest columns.

See module contract in ``interfaces.py``.
"""

from typing import List, Optional

from rapidfuzz.distance import Levenshtein

from .interfaces import RawTable


# Default synonyms used for quantity detection. These match the
# ``column_mapping.synonyms.quantity`` list in config.yaml; kept inline
# here because table_selector is intentionally standalone.
_QUANTITY_SYNONYMS = [
    "quantity",
    "qty",
    "qty reqd",
    "qty req",
    "aantal",
    "stuks",
    "amount",
    "pcs",
    "count",
]


def _normalize_header(header) -> str:
    """Lower-case, collapse whitespace (incl. newlines) to single spaces, strip."""
    if header is None:
        return ""
    s = str(header)
    # Replace newlines/tabs with spaces, collapse runs of whitespace
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = " ".join(s.split())
    return s.strip().lower()


def _header_matches_quantity(header_norm: str, synonyms: List[str], max_distance: int = 2) -> bool:
    """True if ``header_norm`` matches any quantity synonym exactly, or via
    Levenshtein distance <= ``max_distance`` when both strings are >= 4 chars.
    """
    if not header_norm:
        return False
    for syn in synonyms:
        syn_norm = syn.strip().lower()
        if header_norm == syn_norm:
            return True
        # Fuzzy only for strings >= 4 chars (on both sides) to avoid
        # false positives on short tokens like "no" / "qty".
        if len(header_norm) >= 4 and len(syn_norm) >= 4:
            if Levenshtein.distance(header_norm, syn_norm) <= max_distance:
                return True
    return False


def _table_has_quantity_header(table: RawTable, synonyms: List[str], max_distance: int = 2) -> bool:
    for h in table.headers or []:
        if _header_matches_quantity(_normalize_header(h), synonyms, max_distance):
            return True
    return False


def select_data_table(tables: List[RawTable], config: dict) -> Optional[RawTable]:
    """Pick the real item-list data table from the tables on one page.

    Returns ``None`` if nothing qualifies. Does not mutate ``tables``.
    """
    if not tables:
        return None

    sel_cfg = (config or {}).get("table_selection", {}) or {}
    min_columns = int(sel_cfg.get("min_columns", 4))
    max_columns = int(sel_cfg.get("max_columns", 11))
    require_quantity_header = bool(sel_cfg.get("require_quantity_header", True))
    min_data_rows = int(sel_cfg.get("min_data_rows", 1))

    # Allow the config to override synonyms; otherwise use the built-in list.
    synonyms = _QUANTITY_SYNONYMS
    cm = (config or {}).get("column_mapping", {}) or {}
    cm_syns = (cm.get("synonyms") or {}).get("quantity")
    if cm_syns:
        synonyms = list(cm_syns)
    fuzzy_max_distance = int(cm.get("fuzzy_max_distance", 2))

    candidates: List[RawTable] = []
    for t in tables:
        if t is None:
            continue
        n_cols = t.n_cols
        n_rows = t.n_rows
        if n_cols < min_columns or n_cols > max_columns:
            continue
        if n_rows < min_data_rows:
            continue
        if require_quantity_header and not _table_has_quantity_header(t, synonyms, fuzzy_max_distance):
            continue
        candidates.append(t)

    if not candidates:
        return None

    # Prefer shortest table (fewest rows); tie-break on fewest columns,
    # then on original table_index to stay deterministic.
    candidates_sorted = sorted(
        candidates,
        key=lambda t: (t.n_rows, t.n_cols, t.table_index),
    )
    return candidates_sorted[0]
