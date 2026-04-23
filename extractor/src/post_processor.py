"""Per-row normalization for the extractor pipeline.

Operates on a single CanonicalRow and returns a (possibly new) CanonicalRow
with trimmed strings, parsed quantity, regex-extracted schematic position,
normalized newlines, and cleaned unicode. Encoding issues (U+FFFD) are
flagged as warnings but not corrected.
"""

from __future__ import annotations

import re
from dataclasses import fields as dc_fields
from typing import Any, Optional

from .interfaces import CanonicalRow


# Zero-width characters commonly found in poorly-encoded PDFs.
_ZERO_WIDTH_CHARS = [
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "﻿",  # zero width no-break space / BOM
    "⁠",  # word joiner
]

_REPLACEMENT_CHAR = "�"

# Fields that are definitionally strings on CanonicalRow (quantity is Any
# and treated separately).
_STRING_FIELDS = (
    "device_tag",
    "description",
    "manufacturer",
    "model_number",
    "order_number",
    "schematic_position",
    "source_section",
)


def _cleanup_unicode(s: str) -> str:
    """Replace non-breaking spaces and zero-width chars with regular space."""
    s = s.replace("\xa0", " ")
    for zw in _ZERO_WIDTH_CHARS:
        s = s.replace(zw, " ")
    return s


def _normalize_newlines(s: str) -> str:
    return s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _parse_quantity(raw: Any) -> tuple[Any, Optional[str]]:
    """Attempt int(), then float(). Return (value, warning_or_None).

    Empty/None stays None. Non-parseable strings stay as the original string
    with a warning.
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        # bool is subclass of int but semantically not a quantity value we want
        return int(raw), None
    if isinstance(raw, int):
        return raw, None
    if isinstance(raw, float):
        return raw, None

    s = str(raw).strip()
    if s == "":
        return None, None

    try:
        return int(s), None
    except (ValueError, TypeError):
        pass

    # Accept European decimals ("1,5") for float only; fallback.
    float_candidate = s.replace(",", ".") if s.count(",") == 1 and "." not in s else s
    try:
        return float(float_candidate), None
    except (ValueError, TypeError):
        pass

    return raw, f"quantity_unparseable:{raw!r}"


def post_process(row: CanonicalRow, config: dict) -> CanonicalRow:
    """Normalize a single CanonicalRow in place and return it."""
    pp_cfg = (config or {}).get("post_processing", {}) or {}
    trim_ws: bool = bool(pp_cfg.get("trim_whitespace", False))
    normalize_nl: bool = bool(pp_cfg.get("normalize_newlines", False))
    schematic_regex: Optional[str] = pp_cfg.get("extract_schematic_position_regex")

    # 1. Unicode cleanup first (so trimming and regexes see clean text),
    #    but detect U+FFFD BEFORE cleanup (cleanup doesn't remove FFFD, but
    #    we want stable ordering).
    # 5. U+FFFD detection (before any mutation could mask it).
    for fname in _STRING_FIELDS:
        val = getattr(row, fname, None)
        if isinstance(val, str) and _REPLACEMENT_CHAR in val:
            warning = f"encoding_replacement_char_found:{fname}"
            if warning not in row.warnings:
                row.warnings.append(warning)

    # 6. Unicode cleanup on all string fields.
    for fname in _STRING_FIELDS:
        val = getattr(row, fname, None)
        if isinstance(val, str):
            setattr(row, fname, _cleanup_unicode(val))

    # 4. Newline normalization.
    if normalize_nl:
        for fname in _STRING_FIELDS:
            val = getattr(row, fname, None)
            if isinstance(val, str):
                setattr(row, fname, _normalize_newlines(val))

    # 1. Trim whitespace on all string fields.
    if trim_ws:
        for fname in _STRING_FIELDS:
            val = getattr(row, fname, None)
            if isinstance(val, str):
                setattr(row, fname, val.strip())
        # extra_fields: trim string values there too.
        if isinstance(row.extra_fields, dict):
            for k, v in list(row.extra_fields.items()):
                if isinstance(v, str):
                    row.extra_fields[k] = v.strip()

    # 2. Quantity parsing.
    parsed_qty, qty_warning = _parse_quantity(row.quantity)
    row.quantity = parsed_qty
    if qty_warning and qty_warning not in row.warnings:
        row.warnings.append(qty_warning)

    # 3. Schematic position extraction from description.
    if (row.schematic_position is None or
            (isinstance(row.schematic_position, str) and row.schematic_position.strip() == "")):
        if schematic_regex and isinstance(row.description, str) and row.description:
            try:
                m = re.search(schematic_regex, row.description)
            except re.error as e:
                m = None
                warning = f"schematic_regex_invalid:{e}"
                if warning not in row.warnings:
                    row.warnings.append(warning)
            if m:
                # Prefer the first group if the pattern has groups, else full match.
                if m.groups():
                    # Use group(0) to capture the whole match as per spec
                    # ("first match"). Most realistic regexes wrap the alt in a group.
                    row.schematic_position = m.group(0)
                else:
                    row.schematic_position = m.group(0)

    return row
