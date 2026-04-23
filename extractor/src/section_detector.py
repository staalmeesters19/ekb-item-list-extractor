"""Section-label detector for multi-run PDFs.

For PDFs that contain several independent item-list runs (e.g. one per
cabinet), a *run-diff* heuristic derives a stable per-run label from the
page title-block zones defined in ``config["section_detection"]["zones"]``.

Algorithm
---------
1. For every page across all runs, extract short tokens from the configured
   page zones using PyMuPDF word extraction with a clip rectangle.
2. For each run, find tokens that appear on **all** pages of that run
   (stable within-run).
3. Keep only tokens that do **not** appear on any page of any other run
   (distinctive between-runs).
4. Drop trivial tokens: single characters, pure digits, pure punctuation.
5. Among remaining candidates, pick the longest token as the section label
   (longer = more specific).  If no candidates, label is ``None``.

For single-run PDFs there is no contrast, so all page labels are ``None``.

Returns ``Dict[int, Optional[str]]`` — 1-indexed page_number → label.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set


_TRIVIAL_RE = re.compile(r"^[\W\d_]+$")   # pure non-word chars or digits


def _extract_zone_tokens(
    pdf_path: str,
    page_number: int,
    zones: list,
    max_token_length: int,
) -> Set[str]:
    """Return all short tokens found inside the configured zones on this page."""
    tokens: Set[str] = set()
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return tokens

    doc = None
    try:
        doc = fitz.open(pdf_path)
        if page_number < 1 or page_number > doc.page_count:
            return tokens
        page = doc[page_number - 1]
        w = float(page.rect.width)
        h = float(page.rect.height)

        for zone in zones:
            x0 = zone.get("x_min", 0.0) * w
            y0 = zone.get("y_min", 0.0) * h
            x1 = zone.get("x_max", 1.0) * w
            y1 = zone.get("y_max", 1.0) * h
            clip = fitz.Rect(x0, y0, x1, y1)
            try:
                words = page.get_text("words", clip=clip) or []
            except Exception:
                words = []
            for wt in words:
                # wt = (x0, y0, x1, y1, word, block_no, line_no, word_no)
                if len(wt) > 4:
                    word = str(wt[4]).strip()
                    if 1 < len(word) <= max_token_length:
                        tokens.add(word)
    except Exception:
        pass
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return tokens


def _is_trivial(token: str) -> bool:
    """True for single-char tokens, pure-digit strings, pure punctuation."""
    if len(token) < 2:
        return True
    if token.isdigit():
        return True
    if _TRIVIAL_RE.match(token):
        return True
    return False


def detect_section_labels(
    pdf_path: str,
    page_runs: List[List[int]],
    config: dict,
) -> Dict[int, Optional[str]]:
    """Return a mapping of page_number -> section_label for each page in page_runs.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    page_runs:
        List of runs; each run is a list of 1-indexed page numbers.
    config:
        Full extractor config dict.
    """
    sd_cfg = (config or {}).get("section_detection", {}) or {}
    if not sd_cfg.get("enabled", True):
        return {}

    # With fewer than 2 runs there is nothing to contrast against.
    runs_list = [list(r) for r in page_runs if r]
    if len(runs_list) < 2:
        result: Dict[int, Optional[str]] = {}
        for run in runs_list:
            for p in run:
                result[p] = None
        return result

    zones = sd_cfg.get("zones", [])
    max_token_length = int(sd_cfg.get("max_token_length", 15))

    # --- Step 1: extract tokens per page ---
    all_pages: List[int] = [p for run in runs_list for p in run]
    page_tokens: Dict[int, Set[str]] = {}
    for p in all_pages:
        page_tokens[p] = _extract_zone_tokens(pdf_path, p, zones, max_token_length)

    # --- Steps 2-5: run-diff per run ---
    result: Dict[int, Optional[str]] = {}
    all_pages_set = set(all_pages)

    for run_idx, run in enumerate(runs_list):
        run_pages = set(run)
        other_pages = all_pages_set - run_pages

        # Tokens stable within this run (present on every page).
        run_token_sets = [page_tokens.get(p, set()) for p in run_pages]
        if not run_token_sets:
            label = None
        else:
            stable: Set[str] = set.intersection(*run_token_sets)

            # Tokens present in any other-run page.
            other_tokens: Set[str] = set()
            for p in other_pages:
                other_tokens.update(page_tokens.get(p, set()))

            distinct = stable - other_tokens

            # Drop trivial tokens.
            candidates = [t for t in distinct if not _is_trivial(t)]

            label = max(candidates, key=len) if candidates else None

        for p in run:
            result[p] = label

    return result
