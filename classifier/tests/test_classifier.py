"""End-to-end tests for the item-list classifier against ground_truth.yaml."""

import sys
from pathlib import Path

import pytest
import yaml

# Path injection so we can import src.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.classifier import classify, load_config  # noqa: E402


def _load_ground_truth():
    gt_path = ROOT / "ground_truth.yaml"
    with open(gt_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ranges_overlap(r1_start, r1_end, r2_start, r2_end):
    return not (r1_end < r2_start or r2_end < r1_start)


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def ground_truth():
    return _load_ground_truth()


@pytest.fixture(scope="module")
def classified(config, ground_truth):
    """Run the classifier once per PDF and cache results for all tests."""
    base = (ROOT / ground_truth.get("pdf_dir", "..")).resolve()
    out = {}
    for fx in ground_truth["fixtures"]:
        pdf_path = base / fx["pdf"]
        if not pdf_path.exists():
            out[fx["pdf"]] = (fx, pdf_path, None, None)
            continue
        runs, scores = classify(str(pdf_path), config)
        out[fx["pdf"]] = (fx, pdf_path, runs, scores)
    return out


def test_fixtures_exist(classified):
    """All PDF fixtures referenced in ground_truth.yaml must exist on disk."""
    missing = [
        name for name, (_fx, path, _r, _s) in classified.items() if not path.exists()
    ]
    assert not missing, f"Fixture PDFs missing: {missing}"


def test_all_fixtures_have_full_recall(classified):
    """Every expected item-list page range must be covered by at least one detected run."""
    misses = []
    for name, (fx, _path, runs, _scores) in classified.items():
        if runs is None:
            continue
        for exp in fx["expected"]:
            exp_start, exp_end = exp["pages"]
            covered = any(
                _ranges_overlap(exp_start, exp_end, r.start_page, r.end_page)
                for r in runs
            )
            if not covered:
                misses.append({
                    "pdf": name,
                    "expected": [exp_start, exp_end],
                    "detected_runs": [[r.start_page, r.end_page] for r in runs],
                })
    assert not misses, f"Uncovered expected ranges: {misses}"


def test_precision_per_fixture(classified):
    """Detected runs must largely overlap with expected ranges (precision)."""
    all_extras = []
    for name, (fx, _path, runs, _scores) in classified.items():
        if runs is None:
            continue
        expected_pages = set()
        for exp in fx["expected"]:
            s, e = exp["pages"]
            expected_pages.update(range(s, e + 1))
        detected_pages = set()
        for r in runs:
            detected_pages.update(r.pages)
        extras = detected_pages - expected_pages
        # Allow up to 2 false-positive pages per PDF (tolerance).
        if len(extras) > 2:
            all_extras.append({"pdf": name, "extras": sorted(extras)})
    assert not all_extras, f"Too many false-positive pages: {all_extras}"


def test_boundary_accuracy(classified):
    """Run boundaries should match expected boundaries within 1 page tolerance."""
    issues = []
    for name, (fx, _path, runs, _scores) in classified.items():
        if runs is None:
            continue
        for exp in fx["expected"]:
            exp_start, exp_end = exp["pages"]
            # Find best-overlapping run.
            best = None
            best_overlap = 0
            for r in runs:
                if _ranges_overlap(exp_start, exp_end, r.start_page, r.end_page):
                    overlap = (
                        min(exp_end, r.end_page) - max(exp_start, r.start_page) + 1
                    )
                    if overlap > best_overlap:
                        best = r
                        best_overlap = overlap
            if best is None:
                continue  # Covered by the recall test.
            if (
                abs(best.start_page - exp_start) > 1
                or abs(best.end_page - exp_end) > 1
            ):
                issues.append({
                    "pdf": name,
                    "expected": [exp_start, exp_end],
                    "actual": [best.start_page, best.end_page],
                })
    assert not issues, f"Boundary mismatches >1 page: {issues}"
