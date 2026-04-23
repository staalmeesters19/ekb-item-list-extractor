"""Signal 3: row count.

Filters mini-BOMs (small tables embedded in schematic pages) from real
item lists by counting data rows in each extracted table.

- A "data row" has >= 2 non-empty cells after stripping; the first row
  of the table is skipped as a presumed header.
- Tables with fewer than 3 columns are ignored (drawing-frame noise).
- The largest list-like table's data-row count drives the score:

    max_rows >= strong_threshold      -> strong_weight
    max_rows >= weak_threshold        -> weak_weight
    1 <= max_rows < negative_threshold -> negative_weight  (mini-BOM penalty)
    max_rows == 0                      -> 0.0
"""

from __future__ import annotations

from typing import List, Optional

from ..interfaces import PageContext, SignalResult


def _is_empty_cell(cell) -> bool:
    """True if cell is None, empty, or whitespace-only."""
    if cell is None:
        return True
    if not isinstance(cell, str):
        # Non-str cell (unexpected from pdfplumber); treat as non-empty only
        # if it has any meaningful repr.
        return not str(cell).strip()
    return not cell.strip()


def _count_data_rows(table: List[List[Optional[str]]]) -> int:
    """Count rows (after skipping the first) with >= 2 non-empty cells."""
    if not table or len(table) < 2:
        return 0
    count = 0
    for row in table[1:]:
        if not row:
            continue
        non_empty = sum(1 for cell in row if not _is_empty_cell(cell))
        if non_empty >= 2:
            count += 1
    return count


def _table_width(table: List[List[Optional[str]]]) -> int:
    """Max number of columns across all rows of the table."""
    if not table:
        return 0
    return max((len(row) for row in table if row is not None), default=0)


def compute(ctx: PageContext) -> SignalResult:
    cfg = ctx.config.get("signals", {}).get("row_count", {})
    enabled = bool(cfg.get("enabled", True))

    if not enabled:
        return SignalResult(
            name="row_count",
            score=0.0,
            raw_value=0,
            details={"skipped": True},
        )

    strong_threshold = int(cfg.get("strong_threshold", 10))
    strong_weight = float(cfg.get("strong_weight", 3.0))
    weak_threshold = int(cfg.get("weak_threshold", 5))
    weak_weight = float(cfg.get("weak_weight", 1.0))
    negative_threshold = int(cfg.get("negative_threshold", 5))
    negative_weight = float(cfg.get("negative_weight", -2.0))

    tables = ctx.tables or []

    # Per-table data-row counts, but only for "list-like" candidates
    # (>= 3 cols and >= 2 data rows). We also track the full list for
    # transparency in details.
    all_row_counts: List[int] = []
    candidate_counts: List[int] = []
    candidate_indices: List[int] = []

    for idx, table in enumerate(tables):
        data_rows = _count_data_rows(table)
        cols = _table_width(table)
        all_row_counts.append(data_rows)

        if cols >= 3 and data_rows >= 2:
            candidate_counts.append(data_rows)
            candidate_indices.append(idx)

    if not candidate_counts:
        return SignalResult(
            name="row_count",
            score=0.0,
            raw_value=0,
            matched_table_index=None,
            details={
                "max_rows": 0,
                "total_candidate_tables": 0,
                "row_counts_per_table": all_row_counts,
                "qualification": "none",
            },
        )

    # Pick the biggest candidate.
    best_local = max(range(len(candidate_counts)), key=lambda i: candidate_counts[i])
    max_rows = candidate_counts[best_local]
    matched_table_index = candidate_indices[best_local]

    if max_rows >= strong_threshold:
        score = strong_weight
        qualification = "strong"
    elif max_rows >= weak_threshold:
        score = weak_weight
        qualification = "weak"
    elif max_rows >= 1 and max_rows < negative_threshold:
        score = negative_weight
        qualification = "negative"
    else:
        score = 0.0
        qualification = "none"

    return SignalResult(
        name="row_count",
        score=score,
        raw_value=max_rows,
        matched_table_index=matched_table_index,
        details={
            "max_rows": max_rows,
            "total_candidate_tables": len(candidate_counts),
            "row_counts_per_table": all_row_counts,
            "qualification": qualification,
        },
    )
