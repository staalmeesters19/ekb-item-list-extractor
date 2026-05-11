"""Generic page-level table extraction using two parsers for consensus.

Primary parser: PyMuPDF (``fitz``). Secondary parser: pdfplumber.
Both are run on the same page; the downstream TableSelector can use
the two results to reach a row-count consensus. Cells are returned raw:
no post-processing, newlines inside cells are preserved, ``None`` cells
become empty strings.
"""

from typing import List, Tuple

from .interfaces import RawTable


def _merge_empty_header_columns(headers: List[str], rows: List[List[str]]) -> tuple:
    """Merge columns that have an empty header into the previous non-empty
    header column.

    This handles drawings where PyMuPDF/pdfplumber splits a wide cell (e.g.
    a long description) across multiple coordinate-grid buckets, only one of
    which carries the actual header label. The split cells are joined with
    a single space.
    """
    if not headers:
        return headers, rows

    # Find anchor columns (those with a non-empty header). Empty-header
    # columns to the right of an anchor get merged into it.
    n_cols = len(headers)
    first_anchor: int = -1
    for i, h in enumerate(headers):
        if (h or "").strip():
            first_anchor = i
            break
    if first_anchor < 0:
        return headers, rows  # nothing to anchor on

    # Build group assignment: each column index -> anchor column index
    group_of = [None] * n_cols
    current = first_anchor
    for i in range(n_cols):
        if i < first_anchor:
            # leading empty-header columns: leave standalone (rare; treat as own group)
            group_of[i] = i
            continue
        if (headers[i] or "").strip():
            current = i
        group_of[i] = current

    # Determine unique anchors in order
    anchors = []
    seen = set()
    for g in group_of:
        if g not in seen:
            seen.add(g)
            anchors.append(g)

    if anchors == list(range(n_cols)):
        return headers, rows  # nothing to merge

    new_headers = [headers[a] for a in anchors]
    new_rows = []
    for row in rows:
        new_row = []
        for a in anchors:
            parts = []
            for c, g in enumerate(group_of):
                if g == a and c < len(row):
                    v = row[c]
                    if v is not None and str(v).strip():
                        parts.append(str(v).strip())
            new_row.append(" ".join(parts))
        new_rows.append(new_row)
    return new_headers, new_rows


def _drop_empty_columns(headers: List[str], rows: List[List[str]]) -> tuple:
    """Drop columns where the header AND every row cell is empty.

    PyMuPDF with strategy='lines_strict' often returns the real data table
    with phantom None columns interleaved (artefacts of the drawing's
    coordinate grid). This collapses them so the column mapper sees a
    clean table.
    """
    if not headers:
        return headers, rows
    n_cols = len(headers)
    keep = []
    for c in range(n_cols):
        header_empty = not (headers[c] or "").strip()
        col_empty = all(
            not (str(row[c]).strip() if c < len(row) and row[c] is not None else "")
            for row in rows
        )
        if not (header_empty and col_empty):
            keep.append(c)
    if len(keep) == n_cols:
        return headers, rows
    new_headers = [headers[c] for c in keep]
    new_rows = [
        [row[c] if c < len(row) else "" for c in keep]
        for row in rows
    ]
    return new_headers, new_rows


def _extract_with_pymupdf(pdf_path: str, page_number: int) -> List[RawTable]:
    """Run PyMuPDF's ``find_tables`` on the given 1-indexed page.

    Tries both the default strategy and ``lines_strict`` so we catch
    'borderless' tables that the default detector treats as a single
    page-wide coordinate grid.
    """
    tables: List[RawTable] = []
    try:
        import fitz  # pymupdf
    except Exception:
        return tables

    doc = None
    try:
        doc = fitz.open(pdf_path)
        if page_number < 1 or page_number > doc.page_count:
            return tables
        page = doc[page_number - 1]

        strategies = (None, "lines_strict")
        seen_bboxes: set = set()
        idx = 0

        for strat in strategies:
            try:
                tabs = page.find_tables() if strat is None else page.find_tables(strategy=strat)
            except Exception:
                continue

            for tab in getattr(tabs, "tables", []) or []:
                try:
                    data = tab.extract()
                except Exception:
                    continue
                if not data:
                    continue

                header_row = data[0] or []
                headers = [c if c else "" for c in header_row]
                raw_rows = data[1:]
                clean_rows = [[c if c else "" for c in (r or [])] for r in raw_rows]

                headers, clean_rows = _drop_empty_columns(headers, clean_rows)

                bbox = None
                tab_bbox = getattr(tab, "bbox", None)
                if tab_bbox is not None:
                    try:
                        bbox = (
                            float(tab_bbox[0]),
                            float(tab_bbox[1]),
                            float(tab_bbox[2]),
                            float(tab_bbox[3]),
                        )
                    except Exception:
                        bbox = None

                # Skip duplicates across strategies (same bbox, same column count).
                key = (bbox, len(headers), tuple(headers))
                if key in seen_bboxes:
                    continue
                seen_bboxes.add(key)

                tables.append(
                    RawTable(
                        page_number=page_number,
                        headers=headers,
                        rows=clean_rows,
                        parser="pymupdf",
                        table_index=idx,
                        n_cols=len(headers),
                        n_rows=len(clean_rows),
                        bbox=bbox,
                    )
                )
                idx += 1
    except Exception:
        return tables
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    return tables


def _extract_with_pdfplumber(pdf_path: str, page_number: int) -> List[RawTable]:
    """Run pdfplumber's ``extract_tables`` on the given 1-indexed page."""
    tables: List[RawTable] = []
    try:
        import pdfplumber
    except Exception:
        return tables

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                return tables
            page = pdf.pages[page_number - 1]
            try:
                raw_tables = page.extract_tables() or []
            except Exception:
                return tables

            for i, t in enumerate(raw_tables):
                if not t:
                    continue
                header_row = t[0] or []
                headers = [c if c else "" for c in header_row]
                raw_rows = t[1:]
                clean_rows = [[c if c else "" for c in (r or [])] for r in raw_rows]
                tables.append(
                    RawTable(
                        page_number=page_number,
                        headers=headers,
                        rows=clean_rows,
                        parser="pdfplumber",
                        table_index=i,
                        n_cols=len(headers),
                        n_rows=len(clean_rows),
                    )
                )
    except Exception:
        return tables

    return tables


def extract_page_tables(
    pdf_path: str, page_number: int
) -> Tuple[List[RawTable], List[RawTable]]:
    """Extract tables from a single 1-indexed page using both parsers.

    Returns ``(pymupdf_tables, pdfplumber_tables)``. Either list may be empty
    if the corresponding parser fails to find tables or errors out. The
    function does not raise for recoverable failures.
    """
    try:
        pymupdf_tables = _extract_with_pymupdf(pdf_path, page_number)
    except Exception:
        pymupdf_tables = []
    try:
        pdfplumber_tables = _extract_with_pdfplumber(pdf_path, page_number)
    except Exception:
        pdfplumber_tables = []
    return pymupdf_tables, pdfplumber_tables
