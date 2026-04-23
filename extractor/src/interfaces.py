"""Shared interface contract for the extractor.

All modules must use these dataclasses. Do not modify without coordinating
with the pipeline orchestrator.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple


@dataclass
class RawTable:
    """Raw table data from a parser, before canonical mapping."""
    page_number: int
    headers: List[str]           # Column headers; None/empty cells become ""
    rows: List[List[str]]        # Data rows; newlines preserved within cells
    parser: str                  # "pymupdf" or "pdfplumber"
    table_index: int             # Index of this table among all tables on the page
    n_cols: int                  # Number of columns
    n_rows: int                  # Number of data rows (excl. header)
    bbox: Optional[Tuple[float, float, float, float]] = None


@dataclass
class ColumnMapping:
    """Per-column semantic classification."""
    column_index: int
    raw_header: str              # Original header string
    normalized_header: str       # Post-normalization
    canonical_field: Optional[str]   # e.g. "device_tag", "quantity", or None if unmapped
    match_method: str            # "exact" | "fuzzy" | "none"
    match_distance: int = 0      # Levenshtein distance if fuzzy (0 if exact)
    matched_synonym: Optional[str] = None


@dataclass
class CanonicalRow:
    """One extracted item-list row in the uniform schema."""
    source_pdf: str
    source_page: int
    source_section: Optional[str]
    row_index: int               # 0-based within this PDF

    # Core canonical fields
    device_tag: Optional[str] = None
    quantity: Any = None         # int | float | str (fallback when unparseable)
    description: Optional[str] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    order_number: Optional[str] = None
    schematic_position: Optional[str] = None

    # Everything else that came from the table but didn't map to canonical
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    # Audit
    raw: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_pdf": self.source_pdf,
            "source_page": self.source_page,
            "source_section": self.source_section,
            "row_index": self.row_index,
            "device_tag": self.device_tag,
            "quantity": self.quantity,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "model_number": self.model_number,
            "order_number": self.order_number,
            "schematic_position": self.schematic_position,
            "extra_fields": self.extra_fields,
            "raw": self.raw,
            "warnings": self.warnings,
        }


@dataclass
class ExtractionResult:
    """Complete extraction result for one PDF."""
    source_pdf: str
    rows: List[CanonicalRow] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


# Canonical field names (order used for CSV/Excel output headers)
CANONICAL_FIELDS = [
    "device_tag",
    "quantity",
    "description",
    "manufacturer",
    "model_number",
    "order_number",
    "schematic_position",
]


# Module contracts:
#
# table_selector.py   : select_data_table(tables: List[RawTable]) -> Optional[RawTable]
# table_extractor.py  : extract_page_tables(pdf_path, page_number) -> (List[RawTable], List[RawTable])
#                       returns (pymupdf_tables, pdfplumber_tables)
# column_mapper.py    : map_columns(headers: List[str], config: dict) -> List[ColumnMapping]
# row_parser.py       : parse_rows(table: RawTable, mappings: List[ColumnMapping], ctx: dict)
#                           -> List[Dict[str, Any]]
#                       where ctx carries page_number, source_pdf, source_section, starting row_index
# section_detector.py : detect_section_labels(pdf_path, runs) -> Dict[int, Optional[str]]
#                       returns page_number -> section label (or None)
# post_processor.py   : post_process(row: CanonicalRow, config: dict) -> CanonicalRow
# validator.py        : validate(result: ExtractionResult, parser_counts: dict, config: dict)
#                           -> ExtractionResult
