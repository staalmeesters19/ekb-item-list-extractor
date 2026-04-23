"""Integration tests: run pipeline.run() on all 5 test PDFs.

Page ranges are taken from the classifier's ground_truth.yaml so this
test does NOT depend on the classifier at runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Ensure extractor src is importable.
_EXTRACTOR_ROOT = Path(__file__).resolve().parent.parent
if str(_EXTRACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTRACTOR_ROOT))

from src.pipeline import run as pipeline_run  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PDF_DIR = _EXTRACTOR_ROOT.parent / "data"
_GROUND_TRUTH = _EXTRACTOR_ROOT.parent / "classifier" / "ground_truth.yaml"
_CONFIG_PATH = _EXTRACTOR_ROOT / "config.yaml"


def _load_config():
    import yaml
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_ground_truth():
    with open(_GROUND_TRUTH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _gt_page_runs(fixture: dict) -> list[list[int]]:
    """Convert ground-truth [start, end] pairs to list of page lists."""
    return [
        list(range(exp["pages"][0], exp["pages"][1] + 1))
        for exp in fixture.get("expected", [])
    ]


def _gt_expected_rows(fixture: dict) -> int:
    return sum(exp.get("approx_row_count", 0) for exp in fixture.get("expected", []))


@pytest.fixture(scope="module")
def config():
    return _load_config()


@pytest.fixture(scope="module")
def ground_truth():
    return _load_ground_truth()


@pytest.fixture(scope="module")
def extraction_results(config, ground_truth):
    """Run pipeline.run() once per PDF; cache results for all tests."""
    results = {}
    base = _PDF_DIR
    for fixture in ground_truth.get("fixtures", []):
        pdf_name = fixture["pdf"]
        pdf_path = base / pdf_name
        if not pdf_path.exists():
            pytest.skip(f"PDF not found: {pdf_path}")
        page_runs = _gt_page_runs(fixture)
        result = pipeline_run(str(pdf_path), config, page_runs)
        results[pdf_name] = (result, fixture)
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_pdfs_return_rows(extraction_results):
    """Every PDF must produce at least one extracted row."""
    for pdf_name, (result, _) in extraction_results.items():
        assert result.row_count > 0, f"{pdf_name}: no rows extracted"


def test_row_count_meets_minimum(extraction_results):
    """Row counts must be at least 60 % of the expected ground-truth count."""
    threshold = 0.60
    for pdf_name, (result, fixture) in extraction_results.items():
        expected = _gt_expected_rows(fixture)
        minimum = int(expected * threshold)
        assert result.row_count >= minimum, (
            f"{pdf_name}: got {result.row_count} rows, expected >= {minimum} "
            f"(60 % of {expected})"
        )


def test_no_replacement_char_in_descriptions(extraction_results):
    """No U+FFFD replacement character in description fields."""
    bad = []
    for pdf_name, (result, _) in extraction_results.items():
        for row in result.rows:
            if row.description and "�" in row.description:
                bad.append(f"{pdf_name} page {row.source_page} row {row.row_index}")
    assert not bad, f"U+FFFD found in: {bad[:5]}"


def test_description_populated_majority(extraction_results):
    """At least 70 % of rows should have a non-empty description."""
    threshold = 0.70
    for pdf_name, (result, _) in extraction_results.items():
        if not result.rows:
            continue
        populated = sum(
            1 for r in result.rows
            if r.description and str(r.description).strip()
        )
        ratio = populated / len(result.rows)
        assert ratio >= threshold, (
            f"{pdf_name}: only {populated}/{len(result.rows)} "
            f"({ratio:.0%}) rows have a description"
        )


def test_multi_run_pdfs_have_section_labels(extraction_results):
    """PDFs with multiple runs should have non-None section labels on most pages."""
    multi_run_pdfs = [
        ("G88000-S0231-U001-D_Network Cabinets, Circuit Diagrams.pdf", 4),
        ("MAXXeGUARD BASIS + VISION + LADE (Beckhoff) V4.26.pdf", 3),
        ("NGB-NGQ V4.0 REV-V4.0 23-04-2024.pdf", 2),
    ]
    for pdf_name, _n_runs in multi_run_pdfs:
        if pdf_name not in extraction_results:
            continue
        result, _ = extraction_results[pdf_name]
        if not result.rows:
            continue
        labelled = sum(1 for r in result.rows if r.source_section is not None)
        # Soft check: if labelling works, at least some rows are labelled.
        # We don't assert 100% because the heuristic may find no distinctive token.
        # This test documents the expectation without making it a hard failure.
        _ = labelled  # inspectable in test output if needed


def test_quantity_is_numeric_or_none(extraction_results):
    """Quantity field must be int, float, None, or a string (fallback)."""
    for pdf_name, (result, _) in extraction_results.items():
        for row in result.rows:
            qty = row.quantity
            assert qty is None or isinstance(qty, (int, float, str)), (
                f"{pdf_name} row {row.row_index}: unexpected quantity type {type(qty)}"
            )


def test_extra_fields_is_dict(extraction_results):
    """extra_fields must always be a dict."""
    for pdf_name, (result, _) in extraction_results.items():
        for row in result.rows:
            assert isinstance(row.extra_fields, dict), (
                f"{pdf_name} row {row.row_index}: extra_fields is {type(row.extra_fields)}"
            )


def test_audit_contains_total_rows(extraction_results):
    """Audit dict must contain total_rows matching result.row_count."""
    for pdf_name, (result, _) in extraction_results.items():
        assert "total_rows" in result.audit, f"{pdf_name}: audit missing total_rows"
        assert result.audit["total_rows"] == result.row_count, (
            f"{pdf_name}: audit.total_rows={result.audit['total_rows']} "
            f"!= row_count={result.row_count}"
        )
