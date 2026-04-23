"""Post-extraction validator.

Performs structural sanity checks and cross-parser consensus comparison.
Does NOT drop rows: validation is purely informational and writes into
``result.audit`` plus per-row ``warnings``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .interfaces import CANONICAL_FIELDS, ExtractionResult


_ENCODING_WARNING_PREFIX = "encoding_replacement_char_found"


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def validate(
    result: ExtractionResult,
    parser_row_counts: Dict[int, Dict[str, int]],
    config: dict,
) -> ExtractionResult:
    """Append validation findings to ``result.audit`` and per-row warnings."""
    val_cfg = (config or {}).get("validation", {}) or {}
    tolerance: int = int(val_cfg.get("cross_parser_row_count_tolerance", 0))
    required_fields: List[str] = list(val_cfg.get("required_fields", []) or [])
    flag_unicode: bool = bool(val_cfg.get("flag_unicode_replacement", False))

    audit = result.audit if isinstance(result.audit, dict) else {}
    result.audit = audit

    # 1. Cross-parser consensus per page.
    consensus_warnings: List[str] = []
    pages_with_issues: List[int] = []
    for page_number, counts in (parser_row_counts or {}).items():
        pymupdf_n = int(counts.get("pymupdf", 0) or 0)
        pdfplumber_n = int(counts.get("pdfplumber", 0) or 0)
        diff = abs(pymupdf_n - pdfplumber_n)
        if diff > tolerance:
            consensus_warnings.append(
                f"page {page_number}: pymupdf={pymupdf_n} "
                f"pdfplumber={pdfplumber_n} diff={diff} tolerance={tolerance}"
            )
            pages_with_issues.append(page_number)
    audit["consensus_warnings"] = consensus_warnings

    # 2. Required-fields check per row.
    for row in result.rows:
        for field_name in required_fields:
            value = getattr(row, field_name, None)
            if _is_empty(value):
                warning = f"required_field_missing:{field_name}"
                if warning not in row.warnings:
                    row.warnings.append(warning)

    # 3. Unicode replacement summary (counts warnings already added by
    #    post_processor; does not add new warnings itself).
    encoding_warnings_total = 0
    if flag_unicode:
        for row in result.rows:
            for w in row.warnings:
                if isinstance(w, str) and w.startswith(_ENCODING_WARNING_PREFIX):
                    encoding_warnings_total += 1
    audit["encoding_warnings_total"] = encoding_warnings_total

    # 4. Summary statistics.
    total_rows = len(result.rows)
    rows_with_warnings = sum(1 for r in result.rows if r.warnings)

    populated_fields: set = set()
    for row in result.rows:
        for cf in CANONICAL_FIELDS:
            val = getattr(row, cf, None)
            if not _is_empty(val):
                populated_fields.add(cf)
    fields_never_populated = [cf for cf in CANONICAL_FIELDS if cf not in populated_fields]

    audit["total_rows"] = total_rows
    audit["rows_with_warnings"] = rows_with_warnings
    audit["pages_with_consensus_issues"] = pages_with_issues
    audit["fields_never_populated"] = fields_never_populated

    return result
