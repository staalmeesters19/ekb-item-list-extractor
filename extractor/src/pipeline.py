"""Extractor pipeline orchestrator.

Takes a PDF path and a list of page-run groups, then:
    1. Detects per-run section labels (run-diff heuristic).
    2. For each page in each run:
       a. Extracts tables with PyMuPDF + pdfplumber.
       b. Selects the best data table.
       c. Maps column headers to canonical fields.
       d. Parses rows (section-header detection, device-tag inheritance).
    3. Converts intermediate row dicts to CanonicalRow objects.
    4. Post-processes every row (quantity parsing, Unicode cleanup, regex extraction).
    5. Runs structural validation and cross-parser consensus.
    6. Returns an ExtractionResult.

PDF-agnostic: no per-PDF profiles; all behaviour driven by config.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .column_mapper import map_columns
from .interfaces import CanonicalRow, ExtractionResult, RawTable
from .post_processor import post_process
from .row_parser import parse_rows
from .section_detector import detect_section_labels
from .table_extractor import extract_page_tables
from .table_selector import select_data_table, _table_has_quantity_header
from .validator import validate


def _apply_device_tag_fallback(mappings):
    """Treat column 0 as device_tag when no other column was mapped to it.

    Drawings overwhelmingly place the device tag in the first column, often
    with an empty or non-standard header that the synonym matcher misses.
    This fallback only fires when:
      - no column has been mapped to ``device_tag`` already, AND
      - column 0 has no canonical_field assigned.
    """
    if not mappings:
        return mappings
    has_tag = any(m.canonical_field == "device_tag" for m in mappings)
    if has_tag:
        return mappings
    first = mappings[0]
    if first.canonical_field is None:
        first.canonical_field = "device_tag"
        first.match_method = "positional"
    return mappings


def _maybe_promote_header_row(tables: List[RawTable], config: dict) -> List[RawTable]:
    """Promote the first data row to headers when the parsed header row looks like
    a page title (no canonical match) but the first data row contains a
    quantity-like token.

    This handles PDFs where PyMuPDF/pdfplumber treat the merged title cell as
    the header row while the real column headers sit in the first data row.
    """
    cm_cfg = (config or {}).get("column_mapping", {}) or {}
    synonyms = list((cm_cfg.get("synonyms") or {}).get("quantity") or [])
    max_dist = int(cm_cfg.get("fuzzy_max_distance", 2))

    result: List[RawTable] = []
    for t in tables:
        if _table_has_quantity_header(t, synonyms, max_dist):
            result.append(t)
            continue
        # Try promoting rows[0] to headers.
        if not t.rows:
            result.append(t)
            continue
        candidate_headers = t.rows[0]
        # Build a temporary table to check the candidate header row.
        tmp = RawTable(
            page_number=t.page_number,
            headers=list(candidate_headers),
            rows=t.rows[1:],
            parser=t.parser,
            table_index=t.table_index,
            n_cols=t.n_cols,
            n_rows=len(t.rows) - 1,
            bbox=t.bbox,
        )
        if _table_has_quantity_header(tmp, synonyms, max_dist):
            result.append(tmp)
        else:
            result.append(t)
    return result


def run(
    pdf_path: str,
    config: dict,
    page_runs: List[List[int]],
) -> ExtractionResult:
    """Extract item-list rows from *pdf_path*.

    Parameters
    ----------
    pdf_path:
        Absolute or relative path to the source PDF.
    config:
        Full extractor config dict (loaded from config.yaml).
    page_runs:
        List of runs; each run is a list of 1-indexed page numbers in reading
        order.  Example: ``[[33, 34, 35], [47, 48, 49, 50, 51]]``.
    """
    pdf_name = Path(pdf_path).name
    result = ExtractionResult(source_pdf=pdf_name)

    # Guard: empty runs list.
    runs_list = [list(r) for r in page_runs if r]
    if not runs_list:
        result.audit["total_rows"] = 0
        result.audit["note"] = "no_page_runs_provided"
        return result

    # --- Section labels (run-diff) ---
    try:
        section_labels: Dict[int, Optional[str]] = detect_section_labels(
            pdf_path, runs_list, config
        )
    except Exception:
        section_labels = {}

    # --- Per-page extraction ---
    row_index = 0
    parser_row_counts: Dict[int, Dict[str, int]] = {}
    pages_without_table: List[int] = []

    for run in runs_list:
        for page_number in run:
            section_label = section_labels.get(page_number)

            # Extract tables with both parsers.
            try:
                pymupdf_tables, pdfplumber_tables = extract_page_tables(
                    pdf_path, page_number
                )
            except Exception:
                pymupdf_tables, pdfplumber_tables = [], []

            # Promote header row when parser misidentified the table title as headers.
            pymupdf_tables = _maybe_promote_header_row(pymupdf_tables, config)
            pdfplumber_tables = _maybe_promote_header_row(pdfplumber_tables, config)

            # Select best table per parser.
            best_pymupdf = select_data_table(pymupdf_tables, config)
            best_pdfplumber = select_data_table(pdfplumber_tables, config)

            parser_row_counts[page_number] = {
                "pymupdf": best_pymupdf.n_rows if best_pymupdf else 0,
                "pdfplumber": best_pdfplumber.n_rows if best_pdfplumber else 0,
            }

            # Prefer PyMuPDF (preserves spaces); fall back to pdfplumber.
            best_table = best_pymupdf or best_pdfplumber
            if best_table is None:
                pages_without_table.append(page_number)
                continue

            # Map column headers.
            mappings = map_columns(best_table.headers, config)
            mappings = _apply_device_tag_fallback(mappings)

            # Parse rows (section-header detection, device-tag inheritance).
            row_dicts: List[Dict[str, Any]] = parse_rows(best_table, mappings, config)

            for rd in row_dicts:
                # Skip section-header pseudo-rows.
                if rd.get("_is_section_header"):
                    continue

                canonical = CanonicalRow(
                    source_pdf=pdf_name,
                    source_page=page_number,
                    source_section=section_label,
                    row_index=row_index,
                    device_tag=rd.get("device_tag") or None,
                    quantity=rd.get("quantity"),
                    description=rd.get("description") or None,
                    manufacturer=rd.get("manufacturer") or None,
                    model_number=rd.get("model_number") or None,
                    order_number=rd.get("order_number") or None,
                    schematic_position=rd.get("schematic_position") or None,
                    extra_fields=rd.get("extra_fields") or {},
                    raw=list(rd.get("raw") or []),
                )

                # Post-process (quantity parse, Unicode cleanup, regex extraction).
                canonical = post_process(canonical, config)

                result.rows.append(canonical)
                row_index += 1

    # --- Validation ---
    result = validate(result, parser_row_counts, config)
    result.audit["pages_without_table"] = pages_without_table

    return result
