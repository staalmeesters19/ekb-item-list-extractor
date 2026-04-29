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
from src.pipeline import run as _pipeline_run  # noqa: E402
from src.writers.csv_writer import write_csv as _write_csv  # noqa: E402
from src.writers.json_writer import write_json as _write_json  # noqa: E402
from src.writers.procos_writer import write_procos as _write_procos  # noqa: E402
from src.writers.xlsx_writer import write_xlsx as _write_xlsx  # noqa: E402

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ExtractionResult",
    "load_config",
    "classify",
    "extract",
    "to_xlsx_bytes",
    "to_csv_bytes",
    "to_json_bytes",
    "to_procos_bytes",
    "rows_to_dataframe",
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
