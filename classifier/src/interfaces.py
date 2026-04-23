"""Shared interface contract.

All signal modules MUST use these dataclasses. Do not modify the field names
without coordinating with the orchestrator (classifier.py) and scorer.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict


@dataclass
class SignalResult:
    """Output from a signal module for a single page.

    Every signal function must return a SignalResult. Score is the
    weighted contribution this signal makes to the page's total score.
    """
    name: str
    score: float
    raw_value: Any = None
    details: Dict[str, Any] = field(default_factory=dict)
    matched_table_index: Optional[int] = None


@dataclass
class PageContext:
    """Context passed to signal computers.

    The orchestrator pre-extracts text and tables once per page so signals
    don't re-do expensive pdfplumber work.
    """
    page: Any                                 # pdfplumber.Page
    page_number: int                          # 1-indexed
    page_text: str
    tables: List[List[List[Optional[str]]]]   # pdfplumber extract_tables() result
    config: Dict[str, Any]                    # full config dict


@dataclass
class PageScore:
    """Aggregate of all signals for a single page."""
    page_number: int
    total_score: float
    signals: Dict[str, SignalResult]
    best_table_index: Optional[int] = None
    best_table_headers: Optional[List[str]] = None
    row_count: int = 0


@dataclass
class ItemListRun:
    """A cluster of adjacent item-list pages."""
    start_page: int
    end_page: int
    pages: List[int]
    mean_score: float
    column_fingerprint: List[str]
    total_rows: int
    confidence: float                          # 0..1
    signals_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_page": self.start_page,
            "end_page": self.end_page,
            "pages": self.pages,
            "mean_score": round(self.mean_score, 2),
            "column_fingerprint": self.column_fingerprint,
            "total_rows": self.total_rows,
            "confidence": round(self.confidence, 3),
            "signals_summary": self.signals_summary,
        }


# Contract for signal modules:
#
# Each signals/<name>.py must expose a function with this signature:
#
#     def compute(ctx: PageContext) -> SignalResult: ...
#
# It must NOT mutate ctx. It must read its own config from
# ctx.config["signals"][<name>].
