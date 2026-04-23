"""Generic page-level table extraction using two parsers for consensus.

Primary parser: PyMuPDF (``fitz``). Secondary parser: pdfplumber.
Both are run on the same page; the downstream TableSelector can use
the two results to reach a row-count consensus. Cells are returned raw:
no post-processing, newlines inside cells are preserved, ``None`` cells
become empty strings.
"""

from typing import List, Tuple

from .interfaces import RawTable


def _extract_with_pymupdf(pdf_path: str, page_number: int) -> List[RawTable]:
    """Run PyMuPDF's ``find_tables`` on the given 1-indexed page."""
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
        try:
            tabs = page.find_tables()
        except Exception:
            return tables

        table_list = getattr(tabs, "tables", []) or []
        for i, tab in enumerate(table_list):
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

            tables.append(
                RawTable(
                    page_number=page_number,
                    headers=headers,
                    rows=clean_rows,
                    parser="pymupdf",
                    table_index=i,
                    n_cols=len(headers),
                    n_rows=len(clean_rows),
                    bbox=bbox,
                )
            )
    except Exception:
        # Fail safe: a failing page yields no tables for this parser.
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
