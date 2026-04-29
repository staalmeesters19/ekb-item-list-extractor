#!/usr/bin/env python3
"""Extractor CLI.

Usage
-----
    python cli.py <pdf-path> [options]

Options
-------
    --format  csv | xlsx | json   Output format (default: xlsx)
    --output  <directory>         Output directory (default: same dir as PDF)
    --pages   "33-35,47-51"       Override page ranges (comma-separated, dash for range).
                                  If omitted, the classifier auto-detects item-list pages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure extractor root is on sys.path.
# The classifier is imported on-demand inside _get_page_runs() to avoid
# the 'src' package name collision between the two peer projects.
# ---------------------------------------------------------------------------
_EXTRACTOR_ROOT = Path(__file__).resolve().parent
if str(_EXTRACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTRACTOR_ROOT))

import yaml  # noqa: E402

from src.pipeline import run as pipeline_run  # noqa: E402
from src.writers.csv_writer import write_csv  # noqa: E402
from src.writers.json_writer import write_json  # noqa: E402
from src.writers.procos_writer import write_procos  # noqa: E402
from src.writers.xlsx_writer import write_xlsx  # noqa: E402


def _load_config() -> dict:
    cfg_path = _EXTRACTOR_ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_page_ranges(spec: str) -> list[list[int]]:
    """Parse "33-35,47-51" into [[33,34,35],[47,48,49,50,51]]."""
    runs = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            parts = chunk.split("-", 1)
            start, end = int(parts[0].strip()), int(parts[1].strip())
            runs.append(list(range(start, end + 1)))
        else:
            runs.append([int(chunk)])
    return runs


def _get_page_runs_from_classifier(pdf_path: str) -> list[list[int]]:
    """Run the classifier peer project and return page runs.

    Imports the classifier using a two-phase sys.modules swap so it doesn't
    collide with extractor's own 'src' package.
    """
    _CLASSIFIER_DIR = _EXTRACTOR_ROOT.parent / "classifier"
    if not _CLASSIFIER_DIR.exists():
        raise FileNotFoundError(
            f"Classifier peer project not found at {_CLASSIFIER_DIR}. "
            "Either supply --pages or ensure the classifier/ directory exists."
        )

    # Phase 1: load classifier while 'src' is classifier/src.
    if str(_CLASSIFIER_DIR) not in sys.path:
        sys.path.insert(0, str(_CLASSIFIER_DIR))

    # Evict extractor's 'src' from the module cache so Python re-resolves it.
    _evicted = {k: v for k, v in sys.modules.items()
                if k == "src" or k.startswith("src.")}
    for k in _evicted:
        del sys.modules[k]

    try:
        from src.classifier import classify  # type: ignore
        runs_obj, _ = classify(pdf_path)
        page_runs = [r.pages for r in runs_obj]
    finally:
        # Phase 2: restore extractor's 'src' package.
        # Remove classifier src entries, re-add our cached ones.
        for k in list(sys.modules.keys()):
            if k == "src" or k.startswith("src."):
                del sys.modules[k]
        sys.modules.update(_evicted)
        # Remove classifier from path so future 'src' resolution stays on extractor.
        try:
            sys.path.remove(str(_CLASSIFIER_DIR))
        except ValueError:
            pass

    return page_runs


def _output_path(pdf_path: Path, output_dir: Path, fmt: str) -> Path:
    return output_dir / f"{pdf_path.stem}.{fmt}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Extract item-list rows from electrical-drawing PDFs.",
    )
    parser.add_argument("pdf", help="Path to the input PDF.")
    parser.add_argument("--format", choices=["csv", "xlsx", "json", "procos"], default="xlsx",
                        dest="fmt", help="Output format (default: xlsx).")
    parser.add_argument("--output", default=None, dest="output_dir",
                        help="Output directory (default: same directory as PDF).")
    parser.add_argument("--pages", default=None,
                        help='Override page ranges, e.g. "33-35,47-51".')
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir).resolve() if args.output_dir else pdf_path.parent

    config = _load_config()

    # Determine page runs.
    if args.pages:
        page_runs = _parse_page_ranges(args.pages)
        print(f"Using manual page ranges: {page_runs}")
    else:
        print("Running classifier to find item-list pages...")
        try:
            page_runs = _get_page_runs_from_classifier(str(pdf_path))
        except Exception as exc:
            print(f"Error: classifier failed: {exc}", file=sys.stderr)
            return 1
        if not page_runs:
            print("No item-list pages detected. Nothing to extract.", file=sys.stderr)
            return 0
        print(f"Detected {len(page_runs)} run(s): "
              f"{[f'pages {r[0]}-{r[-1]}' for r in page_runs]}")

    # Extract.
    print(f"Extracting from {pdf_path.name}...")
    result = pipeline_run(str(pdf_path), config, page_runs)
    print(f"  => {result.row_count} rows extracted")
    if result.audit.get("consensus_warnings"):
        for w in result.audit["consensus_warnings"]:
            print(f"  [consensus] {w}")
    if result.audit.get("pages_without_table"):
        print(f"  [warn] no table found on pages: {result.audit['pages_without_table']}")

    # Write output.
    if args.fmt == "procos":
        out_path = output_dir / f"{pdf_path.stem}_procos.xltm"
        write_procos(result, str(out_path), config)
    else:
        out_path = _output_path(pdf_path, output_dir, args.fmt)
        if args.fmt == "csv":
            write_csv(result, str(out_path), config)
        elif args.fmt == "json":
            write_json(result, str(out_path), config)
        else:  # xlsx
            write_xlsx([result], str(out_path), config)

    print(f"Written => {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
