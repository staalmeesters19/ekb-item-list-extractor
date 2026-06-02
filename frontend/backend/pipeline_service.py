"""Service layer wrapping the classifier + extractor pipeline for the Streamlit frontend."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, List

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTOR_ROOT = _PROJECT_ROOT / "extractor"
_CLASSIFIER_ROOT = _PROJECT_ROOT / "classifier"

if str(_EXTRACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTRACTOR_ROOT))

import yaml  # noqa: E402

from src.interfaces import ExtractionResult  # noqa: E402
from src.matcher import (  # noqa: E402
    MatchResult,
    load_procos_db as _load_procos_db,
    load_procos_db_v2 as _load_procos_db_v2,
    match_rows as _match_rows,
    match_rows_combined as _match_rows_combined,
    summarize as _summarize_matches,
)
from src.pipeline import run as _pipeline_run  # noqa: E402
from src.writers.csv_writer import write_csv as _write_csv  # noqa: E402
from src.writers.json_writer import write_json as _write_json  # noqa: E402
from src.writers.match_writer import (  # noqa: E402
    write_match_report as _write_match_report,
    write_niet_gevonden as _write_niet_gevonden,
)
from src.writers.procos_writer import write_procos as _write_procos  # noqa: E402
from src.writers.procos_xml_writer import write_procos_xml as _write_procos_xml  # noqa: E402
from src.writers.xlsx_writer import write_xlsx as _write_xlsx  # noqa: E402
from src.xlsx_reader import read_xlsx as _read_xlsx  # noqa: E402

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ExtractionResult",
    "MatchResult",
    "load_config",
    "classify",
    "extract",
    "extract_from_xlsx",
    "to_xlsx_bytes",
    "to_csv_bytes",
    "to_json_bytes",
    "to_procos_bytes",
    "to_procos_xml_bytes",
    "rows_to_dataframe",
    "load_procos_db_from_bytes",
    "load_procos_db_v2_from_path",
    "get_fab_mapping",
    "run_match",
    "run_match_combined",
    "summarize_match",
    "to_match_xlsx_bytes",
    "to_niet_gevonden_xlsx_bytes",
    "match_results_to_dataframe",
]


def load_config() -> dict:
    with open(_EXTRACTOR_ROOT / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def classify(pdf_path: str) -> List[List[int]]:
    if not _CLASSIFIER_ROOT.exists():
        raise FileNotFoundError(f"Classifier project not found at {_CLASSIFIER_ROOT}")

    if str(_CLASSIFIER_ROOT) not in sys.path:
        sys.path.insert(0, str(_CLASSIFIER_ROOT))

    _evicted = {k: v for k, v in sys.modules.items()
                if k == "src" or k.startswith("src.")}
    for k in _evicted:
        del sys.modules[k]

    try:
        from src.classifier import classify as _classifier_classify  # type: ignore
        runs_obj, _ = _classifier_classify(pdf_path)
        return [list(r.pages) for r in runs_obj]
    finally:
        for k in list(sys.modules.keys()):
            if k == "src" or k.startswith("src."):
                del sys.modules[k]
        sys.modules.update(_evicted)
        try:
            sys.path.remove(str(_CLASSIFIER_ROOT))
        except ValueError:
            pass


def extract(pdf_path: str, page_runs: List[List[int]]) -> ExtractionResult:
    return _pipeline_run(pdf_path, load_config(), page_runs)


def extract_from_xlsx(xlsx_path: str) -> ExtractionResult:
    """Extract an item-list from an xlsx file.

    Mirrors the PDF ``extract()`` contract: same ExtractionResult shape,
    same downstream pipeline (match, exports). No page-run argument because
    Excels don't have pages — the reader auto-detects the best sheet and
    header row.
    """
    return _read_xlsx(xlsx_path, load_config())


def _write_to_bytes(writer, suffix: str, *args) -> bytes:
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        writer(*args, tmp_path, load_config())
        with open(tmp_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def to_xlsx_bytes(results: List[ExtractionResult]) -> bytes:
    return _write_to_bytes(_write_xlsx, ".xlsx", results)


def to_csv_bytes(result: ExtractionResult) -> bytes:
    return _write_to_bytes(_write_csv, ".csv", result)


def to_json_bytes(result: ExtractionResult) -> bytes:
    return _write_to_bytes(_write_json, ".json", result)


def to_procos_bytes(result: ExtractionResult) -> bytes:
    return _write_to_bytes(_write_procos, ".xltm", result)


def to_procos_xml_bytes(result: ExtractionResult) -> bytes:
    return _write_to_bytes(_write_procos_xml, ".xml", result)


_DATAFRAME_COLUMNS = [
    "source_pdf",
    "source_page",
    "source_section",
    "device_tag",
    "quantity",
    "description",
    "manufacturer",
    "model_number",
    "order_number",
    "schematic_position",
    "warnings",
]


# --- Match step (ProCos 86k database) -----------------------------------------

def load_procos_db_from_bytes(file_bytes: bytes) -> dict:
    """Load the legacy 86k ProCos export from raw bytes."""
    return _load_procos_db(file_bytes)


def load_procos_db_v2_from_path(path: str) -> dict:
    """Load the new 232k ProCos Artikellijst from a filesystem path."""
    return _load_procos_db_v2(path)


def run_match_combined(result: ExtractionResult,
                       db_v2: dict | None,
                       db_v1: dict | None) -> List[MatchResult]:
    """Match against the new 232k DB first, fall back to legacy 86k on miss."""
    return _match_rows_combined(result.rows, db_v2, db_v1, get_fab_mapping())


def get_fab_mapping() -> dict:
    """Return the fabrikant name -> fabcode mapping from config.yaml."""
    cfg = load_config()
    mapping = (cfg.get("procos_matching") or {}).get("fab_mapping") or {}
    # Normalise keys to uppercase strip-spaces so case differences in PDF
    # fabrikantnamen don't cause mismatches.
    return {str(k).strip().upper(): str(v).strip() for k, v in mapping.items()}


def run_match(result: ExtractionResult, db: dict) -> List[MatchResult]:
    """Match the rows in *result* against the ProCos database."""
    return _match_rows(result.rows, db, get_fab_mapping())


def summarize_match(match_results: List[MatchResult]) -> dict:
    return _summarize_matches(match_results)


def to_match_xlsx_bytes(result: ExtractionResult,
                        match_results: List[MatchResult]) -> bytes:
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        _write_match_report(result.rows, match_results, tmp_path, load_config())
        with open(tmp_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def to_niet_gevonden_xlsx_bytes(result: ExtractionResult,
                                match_results: List[MatchResult]) -> bytes:
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        _write_niet_gevonden(result.rows, match_results, tmp_path, load_config())
        with open(tmp_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def match_results_to_dataframe(result: ExtractionResult,
                               match_results: List[MatchResult]) -> "pd.DataFrame":
    """Build a DataFrame combining extracted rows + match info — for UI display."""
    import pandas as pd
    base = rows_to_dataframe(result)
    if match_results and len(match_results) == len(base):
        base["match_status"] = [m.status for m in match_results]
        base["procos_artikel"] = [m.procos_artikel for m in match_results]
        base["procos_omschrijving"] = [m.procos_omschrijving for m in match_results]
    return base


def rows_to_dataframe(result: ExtractionResult) -> "pd.DataFrame":
    import pandas as pd

    records = []
    for row in result.rows:
        records.append({
            "source_pdf": row.source_pdf,
            "source_page": row.source_page,
            "source_section": row.source_section,
            "device_tag": row.device_tag,
            "quantity": row.quantity,
            "description": row.description,
            "manufacturer": row.manufacturer,
            "model_number": row.model_number,
            "order_number": row.order_number,
            "schematic_position": row.schematic_position,
            "warnings": "; ".join(row.warnings or []),
        })
    return pd.DataFrame(records, columns=_DATAFRAME_COLUMNS)
