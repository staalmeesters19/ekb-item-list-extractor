"""Command-line interface for the item-list classifier.

Usage:
    python -m cli <pdf-path> [--config <path>] [--json]
"""

import argparse
import json
import sys
from pathlib import Path

# Make `src` importable when run from the classifier directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from src.classifier import classify, load_config  # noqa: E402


def _run_row(run) -> dict:
    d = run.to_dict()
    # to_dict already rounds mean_score/confidence; keep as-is.
    return d


def _page_row(ps) -> dict:
    return {
        "page_number": ps.page_number,
        "total_score": round(ps.total_score, 2),
        "row_count": ps.row_count,
        "signals": {name: round(sig.score, 2) for name, sig in ps.signals.items()},
    }


def _print_human(pdf_path: str, runs, page_scores) -> None:
    print(f"PDF: {pdf_path}")
    print(f"Pages: {len(page_scores)}")
    print(f"Detected runs: {len(runs)}")
    print()

    if runs:
        print("Runs:")
        for r in runs:
            fp = ", ".join(r.column_fingerprint[:6])
            if len(r.column_fingerprint) > 6:
                fp += ", ..."
            print(
                f"  pages {r.start_page:>4}-{r.end_page:<4} "
                f"({len(r.pages):>2} pg)  "
                f"mean={r.mean_score:>5.1f}  "
                f"rows={r.total_rows:>4}  "
                f"conf={r.confidence:.2f}  "
                f"cols=[{fp}]"
            )
        print()
    else:
        print("(no runs detected)")
        print()

    # Top-10 highest-scoring pages with sig-scores as a table.
    top = sorted(page_scores, key=lambda s: s.total_score, reverse=True)[:10]
    sig_names = ["title", "column_header", "row_count", "vector_density", "continuity"]
    header = (
        f"{'page':>5} {'total':>7} {'rows':>5}  "
        + "  ".join(f"{n[:10]:>10}" for n in sig_names)
    )
    print("Top-10 pages by score:")
    print(header)
    print("-" * len(header))
    for ps in top:
        sig_cells = []
        for n in sig_names:
            sig = ps.signals.get(n)
            sig_cells.append(f"{sig.score:>10.2f}" if sig else f"{'-':>10}")
        print(
            f"{ps.page_number:>5} "
            f"{ps.total_score:>7.2f} "
            f"{ps.row_count:>5}  "
            + "  ".join(sig_cells)
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="classifier",
        description="Detect item-list page ranges in a technical PDF.",
    )
    parser.add_argument("pdf", help="Path to the PDF file.")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a config.yaml override (default: classifier/config.yaml).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    config = load_config(args.config)
    runs, page_scores = classify(pdf_path, config)

    if args.json:
        out = {
            "pdf": pdf_path,
            "runs": [_run_row(r) for r in runs],
            "page_scores": [_page_row(p) for p in page_scores],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        _print_human(pdf_path, runs, page_scores)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
