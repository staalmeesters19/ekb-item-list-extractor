"""Clusterer: groups adjacent high-scoring pages into ItemListRuns,
splitting runs whenever the column fingerprint changes materially."""

from typing import List, Set
from .interfaces import PageScore, ItemListRun


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 0.0
    return len(a & b) / len(u)


def _normalize_headers(headers) -> Set[str]:
    """Normalize a list of header strings into a comparable set."""
    if not headers:
        return set()
    return {
        h.strip().lower()
        for h in headers
        if h and isinstance(h, str) and h.strip()
    }


def cluster(scores: List[PageScore], config: dict) -> List[ItemListRun]:
    """Group adjacent high-scoring pages into item list runs.

    Rules:
      - A page "qualifies" for membership if total_score >= weak_threshold.
      - A run must contain at least one "strong" page (>= strong_threshold)
        to be emitted — this kills pure-weak false positives.
      - A run must have total_rows >= min_total_rows_per_run (kills mini-BOMs).
      - Runs split on: page gap > max_gap, or jaccard(fingerprint) < min_sim.
    """
    scoring_cfg = config.get("scoring", {})
    clustering_cfg = config.get("clustering", {})
    strong_threshold = float(scoring_cfg.get("strong_match", 12.0))
    weak_threshold = float(scoring_cfg.get("weak_match", 7.0))
    min_fingerprint_sim = float(clustering_cfg.get("min_fingerprint_similarity", 0.6))
    min_rows_per_run = int(clustering_cfg.get("min_total_rows_per_run", 10))
    max_gap = int(clustering_cfg.get("max_gap", 0))

    scores_sorted = sorted(scores, key=lambda s: s.page_number)

    runs: List[ItemListRun] = []
    current: List[PageScore] = []
    current_fingerprint: Set[str] = set()
    last_page = None  # last page_number actually added to current

    def flush():
        nonlocal current, current_fingerprint
        if not current:
            return
        has_strong = any(s.total_score >= strong_threshold for s in current)
        total_rows = sum(s.row_count for s in current)
        if has_strong and total_rows >= min_rows_per_run:
            pages = [s.page_number for s in current]
            mean_score = sum(s.total_score for s in current) / len(current)
            union_fp: Set[str] = set()
            for s in current:
                union_fp |= _normalize_headers(s.best_table_headers)
            conf = max(0.0, min(1.0, mean_score / 20.0))
            runs.append(ItemListRun(
                start_page=pages[0],
                end_page=pages[-1],
                pages=pages,
                mean_score=mean_score,
                column_fingerprint=sorted(union_fp),
                total_rows=total_rows,
                confidence=conf,
                signals_summary={
                    "strong_pages": sum(
                        1 for s in current if s.total_score >= strong_threshold
                    ),
                    "weak_pages": sum(
                        1 for s in current
                        if weak_threshold <= s.total_score < strong_threshold
                    ),
                },
            ))
        current = []
        current_fingerprint = set()

    for s in scores_sorted:
        qualifies = s.total_score >= weak_threshold

        if not qualifies:
            # A non-qualifying page can be tolerated as a gap if max_gap allows it.
            # Gap = how many non-qualifying pages we've skipped since last_page.
            # We just continue here; the gap check on the NEXT qualifying page
            # (via last_page) decides whether to keep or flush.
            if current and last_page is not None:
                if (s.page_number - last_page) > max_gap:
                    flush()
                    last_page = None
            continue

        s_fp = _normalize_headers(s.best_table_headers)

        if not current:
            current = [s]
            current_fingerprint = set(s_fp)
            last_page = s.page_number
            continue

        # Adjacency check: gap = pages between last_page and s (exclusive).
        gap = s.page_number - last_page - 1
        if gap > max_gap:
            flush()
            current = [s]
            current_fingerprint = set(s_fp)
            last_page = s.page_number
            continue

        # Fingerprint continuity (only when both sides have a fingerprint).
        if current_fingerprint and s_fp:
            sim = _jaccard(current_fingerprint, s_fp)
            if sim < min_fingerprint_sim:
                flush()
                current = [s]
                current_fingerprint = set(s_fp)
                last_page = s.page_number
                continue

        # Merge into current run.
        current.append(s)
        current_fingerprint |= s_fp
        last_page = s.page_number

    flush()
    return runs
