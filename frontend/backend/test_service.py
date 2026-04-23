"""Sanity test for pipeline_service. Run: python frontend/backend/test_service.py"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[1]
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from backend import pipeline_service as svc  # noqa: E402


def main() -> int:
    pdf_path = str(_PROJECT_ROOT / "data" / "126-0053 Cabinet Lineator Controller.pdf")
    assert Path(pdf_path).exists(), f"PDF not found: {pdf_path}"

    page_runs = svc.classify(pdf_path)
    assert page_runs, "classify returned no runs"
    expected_pages = {33, 34, 35}
    assert any(expected_pages.issubset(set(r)) for r in page_runs), (
        f"No run contains pages 33-35; got {page_runs}"
    )

    result = svc.extract(pdf_path, page_runs)
    assert result.row_count >= 50, f"Expected >=50 rows, got {result.row_count}"

    xlsx = svc.to_xlsx_bytes([result])
    assert xlsx and xlsx[:2] == b"PK", "XLSX bytes invalid"

    csv_bytes = svc.to_csv_bytes(result)
    assert csv_bytes, "CSV bytes empty"
    assert b"description" in csv_bytes, "CSV missing 'description' header"

    json_bytes = svc.to_json_bytes(result)
    assert json_bytes, "JSON bytes empty"
    parsed = json.loads(json_bytes.decode("utf-8"))
    assert isinstance(parsed, dict), "JSON did not parse as dict"

    df = svc.rows_to_dataframe(result)
    assert len(df) == result.row_count, (
        f"DataFrame row count {len(df)} != result.row_count {result.row_count}"
    )
    expected_cols = {
        "source_pdf", "source_page", "source_section", "device_tag",
        "quantity", "description", "manufacturer", "model_number",
        "order_number", "schematic_position", "warnings",
    }
    missing = expected_cols - set(df.columns)
    assert not missing, f"DataFrame missing columns: {missing}"

    print(
        f"OK: classify={len(page_runs)} runs, extract={result.row_count} rows, "
        f"writers all produced bytes, dataframe shape={df.shape}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
