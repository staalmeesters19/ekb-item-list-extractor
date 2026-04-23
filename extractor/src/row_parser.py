"""Row parser: convert a RawTable + ColumnMapping list into canonical row dicts.

Responsibilities:
    * Drop empty padding rows.
    * Detect section-header rows (sparse rows between data rows) and
      inherit their label onto following data rows via ``device_tag``.
    * Project each data cell onto its canonical field (or into ``extra_fields``).
    * Preserve the original row under ``raw`` for auditing.

PDF-agnostic: no per-PDF knowledge, only ``config["row_parsing"]`` and the
mapping list computed by ``column_mapper``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .interfaces import ColumnMapping, RawTable


log = logging.getLogger(__name__)


# Matches a plausibly-numeric quantity. Allows decimal separators (. or ,),
# optional leading sign, and an optional trailing unit letter (e.g. "3m").
# A stricter integer check would be too narrow — real BOMs sometimes use
# fractional quantities or "1x".
_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?\s*[a-zA-Z]?$")


def _cell_str(cell: Any) -> str:
    """Coerce a raw cell value to a string without losing internal newlines."""
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    return str(cell)


def _is_empty(cell: Any) -> bool:
    """True if the cell is None / empty / whitespace-only."""
    s = _cell_str(cell).strip()
    return s == ""


def _strip_preserve_internal(s: str) -> str:
    """Strip leading/trailing whitespace per cell, preserve internal ``\\n``."""
    if s is None:
        return ""
    # Strip only leading/trailing whitespace — keep internal newlines/tabs
    # because multi-line description cells are meaningful.
    return s.strip()


def _looks_numeric(cell: Any) -> bool:
    """True if the cell, stripped, parses as a number (possibly with unit)."""
    s = _cell_str(cell).strip()
    if not s:
        return False
    return bool(_NUMERIC_RE.match(s))


def _quantity_column_indices(mappings: List[ColumnMapping]) -> List[int]:
    """Return every column index that maps to ``quantity``."""
    return [m.column_index for m in mappings if m.canonical_field == "quantity"]


def _first_nonempty_cell(cells: List[Any]) -> Optional[str]:
    for c in cells:
        s = _cell_str(c).strip()
        if s:
            return s
    return None


def _header_key(mapping: ColumnMapping, col_idx: int) -> str:
    """Key used in ``extra_fields`` for an unmapped column."""
    # Prefer the normalized header (produced by column_mapper); fall back
    # to the raw header; finally to a positional key so nothing collides.
    if mapping.normalized_header:
        return mapping.normalized_header
    raw = (mapping.raw_header or "").strip()
    if raw:
        return raw
    return f"col_{col_idx}"


def _merge_canonical_value(existing: Any, new: str) -> str:
    """Merge two values that both map to the same canonical field.

    Strategy: keep the first non-empty value; if both are non-empty and
    distinct, concatenate with a single newline. A warning is logged by the
    caller, not here.
    """
    new_s = new if new is not None else ""
    if existing is None or (isinstance(existing, str) and existing == ""):
        return new_s
    if new_s == "":
        return existing
    if existing == new_s:
        return existing
    return f"{existing}\n{new_s}"


def parse_rows(
    table: RawTable,
    mappings: List[ColumnMapping],
    config: dict,
) -> List[Dict[str, Any]]:
    """Convert a RawTable into a list of semi-canonical row dicts.

    See module docstring for keys emitted per row.
    """
    cfg_root = config or {}
    rp_cfg = cfg_root.get("row_parsing", {}) or {}
    pp_cfg = cfg_root.get("post_processing", {}) or {}

    drop_empty = bool(rp_cfg.get("drop_empty_rows", True))
    sh_cfg = rp_cfg.get("section_header_detection", {}) or {}
    sh_enabled = bool(sh_cfg.get("enabled", False))
    sh_max_filled = int(sh_cfg.get("max_filled_cells", 2))
    trim_whitespace = bool(pp_cfg.get("trim_whitespace", True))

    qty_col_indices = _quantity_column_indices(mappings)

    # Columns that carry the canonical "device_tag" (may be 0, 1, or more).
    tag_col_indices = [m.column_index for m in mappings if m.canonical_field == "device_tag"]

    # Build a quick lookup: col_idx -> mapping. Columns beyond len(mappings)
    # get a synthetic unmapped key (robust against ragged rows).
    mapping_by_col: Dict[int, ColumnMapping] = {m.column_index: m for m in mappings}

    current_inherited_device_tag: Optional[str] = None
    result: List[Dict[str, Any]] = []

    for row in table.rows or []:
        original_cells = list(row) if row is not None else []

        # --- 1. Empty-row filter ---------------------------------------------------
        if drop_empty and all(_is_empty(c) for c in original_cells):
            continue

        # --- Pre-compute per-cell stripped views (what downstream logic uses) -----
        stripped_cells: List[str] = [
            _strip_preserve_internal(_cell_str(c)) if trim_whitespace else _cell_str(c)
            for c in original_cells
        ]
        nonempty_count = sum(1 for s in stripped_cells if s.strip())

        # --- 2. Section-header detection ------------------------------------------
        is_section_header = False
        if sh_enabled and nonempty_count > 0 and nonempty_count <= sh_max_filled:
            # The row qualifies as a section-header *only* if every quantity
            # column is empty or non-numeric. A single-text-cell row with no
            # numeric qty is the canonical shape.
            qty_cells = [
                stripped_cells[i] if i < len(stripped_cells) else ""
                for i in qty_col_indices
            ]
            qty_is_numeric = any(_looks_numeric(q) for q in qty_cells)
            if not qty_is_numeric:
                is_section_header = True

        if is_section_header:
            label = _first_nonempty_cell(stripped_cells) or ""
            current_inherited_device_tag = label if label else current_inherited_device_tag
            result.append(
                {
                    "_is_section_header": True,
                    "_inherited_device_tag": None,
                    "section_header_text": label,
                    "extra_fields": {},
                    "raw": original_cells,
                }
            )
            continue

        # --- 3. Data-row processing ------------------------------------------------
        row_dict: Dict[str, Any] = {}
        extra_fields: Dict[str, Any] = {}
        duplicate_canonical: Dict[str, int] = {}

        n_cells = len(stripped_cells)
        n_cols = max(len(mappings), n_cells)

        for col_idx in range(n_cols):
            cell_val: str = stripped_cells[col_idx] if col_idx < n_cells else ""
            mapping = mapping_by_col.get(col_idx)

            if mapping is not None and mapping.canonical_field:
                canonical = mapping.canonical_field
                if canonical in row_dict and row_dict[canonical] not in (None, ""):
                    # Second (or later) column mapping to the same canonical
                    # field — merge and count for warning emission.
                    merged = _merge_canonical_value(row_dict[canonical], cell_val)
                    row_dict[canonical] = merged
                    duplicate_canonical[canonical] = duplicate_canonical.get(canonical, 1) + 1
                else:
                    row_dict[canonical] = cell_val
            else:
                # Unmapped column -> extra_fields, keyed by normalized header.
                if mapping is not None:
                    key = _header_key(mapping, col_idx)
                else:
                    key = f"col_{col_idx}"
                # Do not clobber an earlier unmapped value with the same
                # normalized key (e.g. two blank headers). Fall back to a
                # positional key for the duplicate.
                if key in extra_fields:
                    key = f"{key}__col_{col_idx}"
                extra_fields[key] = cell_val

        for canonical, count in duplicate_canonical.items():
            if count > 1:
                log.warning(
                    "Row on page %s has %d columns mapped to canonical field %r; "
                    "values were concatenated.",
                    getattr(table, "page_number", "?"),
                    count,
                    canonical,
                )

        row_dict["extra_fields"] = extra_fields
        row_dict["raw"] = original_cells
        row_dict["_is_section_header"] = False
        row_dict["_inherited_device_tag"] = None

        # --- 4. Device-tag inheritance --------------------------------------------
        # If the mapping declared a device_tag column but no value was parsed,
        # AND we have a current inherited label from a prior section-header,
        # inherit it. Also covers the case where there is NO device_tag column
        # at all (then we still surface the inheritance, useful downstream).
        current_tag_value = row_dict.get("device_tag")
        tag_is_empty = current_tag_value is None or (
            isinstance(current_tag_value, str) and current_tag_value.strip() == ""
        )
        if tag_is_empty and current_inherited_device_tag:
            row_dict["device_tag"] = current_inherited_device_tag
            row_dict["_inherited_device_tag"] = current_inherited_device_tag
        elif not tag_is_empty:
            # Row brings its own device_tag; do not overwrite.
            row_dict["_inherited_device_tag"] = None

        # Defensive: ensure tag_col_indices usage doesn't produce a name error
        # from an unused import; reference it harmlessly.
        _ = tag_col_indices

        result.append(row_dict)

    return result
