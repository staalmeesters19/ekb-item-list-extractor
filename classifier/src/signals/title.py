"""Signal 1: title detection.

Scans the page text for item-list / parts-list / BOM titles using a list of
regex patterns from the config. If any pattern matches, the signal fires at
its full configured weight.

Other signals (column headers, row count, vector density) are expected to
correct any false positives from titles that appear as stray labels inside
schematics.
"""

from __future__ import annotations

import re
from typing import List

from ..interfaces import PageContext, SignalResult


# Characters that commonly appear as encoding garbage in pdfplumber output.
# We normalize them to '.' so regex word-boundary matching still works
# around them.
_GARBAGE_CHARS = "�"  # U+FFFD REPLACEMENT CHARACTER


def _normalize(text: str) -> str:
    """Replace encoding-noise characters with '.' for robust matching."""
    if not text:
        return ""
    # Replace known garbage chars.
    for ch in _GARBAGE_CHARS:
        text = text.replace(ch, ".")
    # Also replace any non-printable / non-ASCII control-ish chars that
    # sometimes leak through. Keep newlines/tabs/spaces.
    out_chars: List[str] = []
    for ch in text:
        code = ord(ch)
        if ch in ("\n", "\r", "\t"):
            out_chars.append(ch)
        elif code < 32:
            out_chars.append(".")
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _snippet(text: str, start: int, end: int, pad: int = 30) -> str:
    """Return ~`pad` chars before and after [start:end], collapsed to one line."""
    s = max(0, start - pad)
    e = min(len(text), end + pad)
    snip = text[s:e]
    # Collapse whitespace for readability.
    snip = re.sub(r"\s+", " ", snip).strip()
    return snip


def compute(ctx: PageContext) -> SignalResult:
    cfg = ctx.config.get("signals", {}).get("title", {})
    enabled = bool(cfg.get("enabled", True))
    weight = float(cfg.get("weight", 0.0))
    patterns = cfg.get("patterns", []) or []

    if not enabled:
        return SignalResult(
            name="title",
            score=0.0,
            raw_value=False,
            details={"skipped": True},
        )

    text = ctx.page_text or ""
    if not text.strip():
        return SignalResult(
            name="title",
            score=0.0,
            raw_value=False,
            details={"matched_patterns": []},
        )

    text = _normalize(text)

    matched_patterns: List[str] = []
    first_match_snippet: str = ""

    for pat in patterns:
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            # Bad regex in config -- skip silently, don't crash the pipeline.
            continue
        m = rx.search(text)
        if m:
            matched_patterns.append(pat)
            if not first_match_snippet:
                first_match_snippet = _snippet(text, m.start(), m.end())

    if matched_patterns:
        return SignalResult(
            name="title",
            score=weight,
            raw_value=True,
            details={
                "matched_patterns": matched_patterns,
                "match_count": len(matched_patterns),
                "first_match_snippet": first_match_snippet,
            },
        )

    return SignalResult(
        name="title",
        score=0.0,
        raw_value=False,
        details={"matched_patterns": []},
    )
