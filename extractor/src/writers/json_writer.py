"""JSON writer for ExtractionResult."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ..interfaces import ExtractionResult


def write_json(result: ExtractionResult, output_path: str, config: dict) -> None:
    """Write *result* as indented JSON to *output_path*."""
    out_cfg = (config or {}).get("output", {}) or {}
    include_raw: bool = bool(out_cfg.get("include_raw_column", True))
    include_warnings: bool = bool(out_cfg.get("include_warnings_column", True))

    rows_out = []
    for row in result.rows:
        d = row.to_dict()
        if not include_raw:
            d.pop("raw", None)
        if not include_warnings:
            d.pop("warnings", None)
        rows_out.append(d)

    payload = {
        "source_pdf": result.source_pdf,
        "total_rows": len(rows_out),
        "audit": result.audit,
        "rows": rows_out,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
