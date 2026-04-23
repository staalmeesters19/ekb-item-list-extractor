"""CSV writer for ExtractionResult."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List

from ..interfaces import CANONICAL_FIELDS, ExtractionResult


def write_csv(result: ExtractionResult, output_path: str, config: dict) -> None:
    """Write *result* to a UTF-8-BOM CSV file at *output_path*."""
    out_cfg = (config or {}).get("output", {}) or {}
    separator: str = out_cfg.get("csv_separator", ",")
    include_raw: bool = bool(out_cfg.get("include_raw_column", True))
    include_warnings: bool = bool(out_cfg.get("include_warnings_column", True))

    meta_fields = ["source_pdf", "source_page", "source_section", "row_index"]
    extra_field = ["extra_fields"]
    optional = []
    if include_raw:
        optional.append("raw")
    if include_warnings:
        optional.append("warnings")

    fieldnames = meta_fields + CANONICAL_FIELDS + extra_field + optional

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter=separator,
                                extrasaction="ignore")
        writer.writeheader()
        for row in result.rows:
            d = row.to_dict()
            d["extra_fields"] = json.dumps(d.get("extra_fields") or {}, ensure_ascii=False)
            if include_raw:
                d["raw"] = json.dumps(d.get("raw") or [], ensure_ascii=False)
            if include_warnings:
                d["warnings"] = "; ".join(d.get("warnings") or [])
            writer.writerow({k: (d.get(k) if d.get(k) is not None else "") for k in fieldnames})
