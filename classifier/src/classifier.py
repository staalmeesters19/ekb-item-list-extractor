"""Top-level orchestrator: load config, walk PDF pages, score each page,
apply post-pass continuity, cluster into runs."""

from pathlib import Path
from typing import List, Tuple, Optional

import yaml
import pdfplumber

from .interfaces import PageContext, PageScore, ItemListRun
from .scorer import score_page, apply_continuity
from .clusterer import cluster


def load_config(path: Optional[str] = None) -> dict:
    """Load config.yaml from an explicit path, or default to classifier/config.yaml."""
    if path is None:
        # classifier/src/classifier.py -> classifier/
        here = Path(__file__).resolve().parent.parent
        path = here / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def classify(
    pdf_path: str,
    config: Optional[dict] = None,
) -> Tuple[List[ItemListRun], List[PageScore]]:
    """Classify a PDF.

    Returns a tuple of:
      - List[ItemListRun]: detected item-list runs (what the caller wants)
      - List[PageScore]:   per-page scores (for audit / debugging)
    """
    if config is None:
        config = load_config()

    scores: List[PageScore] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            # Text extraction is best-effort; a failing page shouldn't kill the run.
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            # Same for table extraction.
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            ctx = PageContext(
                page=page,
                page_number=i,
                page_text=text,
                tables=tables,
                config=config,
            )
            scores.append(score_page(ctx))

    # Post-pass: neighbour-based continuity bonus.
    scores = apply_continuity(scores, config)

    # Cluster adjacent high-scoring pages into runs.
    runs = cluster(scores, config)
    return runs, scores
