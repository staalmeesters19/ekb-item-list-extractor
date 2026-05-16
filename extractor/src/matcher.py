"""Match extracted PDF rows against a ProCos artikel-database.

This module exposes a small public API for loading the ProCos export
(an ~86k-row Excel file) into a lookup structure and matching each
CanonicalRow produced by the extractor pipeline against it.

Public surface:
    - MatchResult       : dataclass with one row's match outcome
    - load_procos_db()  : load Excel export into lookup dict
    - match_rows()      : run match for a list of CanonicalRow
    - summarize()       : aggregate statistics over MatchResults

The fab-mapping (PDF-fabrikant-text -> ProCos fabcode) is NOT defined
here -- it is passed in by the caller, so this module stays pure.
"""

from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import openpyxl

from .interfaces import CanonicalRow


# ----------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------

@dataclass
class MatchResult:
    """Outcome for one CanonicalRow matched against the ProCos database."""
    status: str                # see docstring of match_rows for full list
    procos_artikel: str = ""   # "" if no hit
    procos_fabcode: str = ""   # ProCos fabrikantcode that was hit ("" if none)
    procos_omschrijving: str = ""  # ProCos's own description ("" if no hit)
    mapped_fab: str = ""       # the mapped fab code used for lookup
    matched_typenr: str = ""   # candidate type-nr that matched / was tried last
    n_hits: int = 0            # number of candidate hits (0/1/many)


# ----------------------------------------------------------------------
# Private helpers
# ----------------------------------------------------------------------

_NORM_RE = re.compile(r"[\s\-./()_,]+")


def _norm_type(s: str | None) -> str:
    """Strip whitespace / dashes / dots / slashes / parens / underscores /
    commas and uppercase. Mirrors ProCos's own match-time normalization."""
    if not s:
        return ""
    return _NORM_RE.sub("", str(s)).upper()


def _candidate_typenrs(row: CanonicalRow) -> list[str]:
    """Priority list of type-nr candidates to try for the given row.

    Order:
        1. row.model_number
        2. row.order_number (if different from model_number)
        3. Newline-split parts of any extra_fields value whose key looks
           like a type / order / model / bestelnr / bstnr column.

    Duplicates (by normalized form) are removed, preserving first occurrence.
    """
    cands: list[str] = []
    if row.model_number:
        cands.append(str(row.model_number))
    if row.order_number and row.order_number != row.model_number:
        cands.append(str(row.order_number))

    if isinstance(row.extra_fields, dict):
        for key, val in row.extra_fields.items():
            key_l = str(key).lower()
            if any(t in key_l for t in ("type", "order", "bestelnr", "bstnr", "model")):
                if val:
                    for part in str(val).split("\n"):
                        part = part.strip()
                        if part:
                            cands.append(part)

    seen: set[str] = set()
    unique: list[str] = []
    for c in cands:
        n = _norm_type(c)
        if n and n not in seen:
            seen.add(n)
            unique.append(c)
    return unique


def _open_workbook(source: Any):
    """Open the ProCos xlsx from either a path-like or a bytes-like source.

    openpyxl can read from a file path or a file-like object. For raw bytes
    (e.g. from Streamlit's uploader) we wrap them in BytesIO. Anything else
    is written to a NamedTemporaryFile first.
    """
    from io import BytesIO

    if isinstance(source, (str, Path)):
        return openpyxl.load_workbook(str(source), read_only=True, data_only=True)

    if isinstance(source, (bytes, bytearray)):
        return openpyxl.load_workbook(BytesIO(source), read_only=True, data_only=True)

    # File-like object (has .read) -- e.g. Streamlit UploadedFile
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, str):
            data = data.encode("utf-8", errors="ignore")
        # Reset the cursor if possible, in case the caller wants to re-read
        if hasattr(source, "seek"):
            try:
                source.seek(0)
            except Exception:
                pass
        return openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)

    # Last resort: dump to a temp file
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(bytes(source))
        tmp_path = Path(tmp.name)
    return openpyxl.load_workbook(str(tmp_path), read_only=True, data_only=True)


# ----------------------------------------------------------------------
# Public functions
# ----------------------------------------------------------------------

def load_procos_db(source: Any) -> dict:
    """Load the ProCos export into a lookup dict.

    Args:
        source: filesystem path (str/Path) or bytes-like / file-like object.
                Must point to an xlsx with a sheet named "export" and at
                least these columns in this order:
                  Artikel | BG | Voorraad | Artikelsoort | Fabrikant |
                  Type nr. | Omschrijving 1 | ... (15 cols total)

    Returns:
        A dict with keys:
            "by_fab_type":  {(fab_code, normalized_type): [record_dicts]}
            "by_type_only": {normalized_type: [record_dicts]}
            "n_rows":       int (number of valid articles loaded)

        Each record_dict is:
            {"artikel", "fabrikant", "type_nr", "omschrijving"}

    Articles with fabrikant == "XXXXXX" are excluded from by_fab_type
    (XXXXXX is ProCos's placeholder for "unknown"), but still appear in
    by_type_only.
    """
    wb = _open_workbook(source)
    try:
        ws = wb["export"]

        by_fab_type: dict[tuple[str, str], list[dict]] = defaultdict(list)
        by_type_only: dict[str, list[dict]] = defaultdict(list)
        n = 0

        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if i == 1:
                # header row
                continue
            if not row or len(row) < 7:
                continue

            artikel = row[0]
            fab = row[4]
            type_nr = row[5]
            omsch1 = row[6]

            if not artikel or not type_nr:
                continue
            norm = _norm_type(type_nr)
            if not norm:
                continue

            rec = {
                "artikel": str(artikel),
                "fabrikant": str(fab) if fab else "",
                "type_nr": str(type_nr),
                "omschrijving": str(omsch1) if omsch1 else "",
            }
            by_type_only[norm].append(rec)
            if fab and str(fab) != "XXXXXX":
                by_fab_type[(str(fab), norm)].append(rec)
            n += 1
    finally:
        wb.close()

    return {
        "by_fab_type": dict(by_fab_type),
        "by_type_only": dict(by_type_only),
        "n_rows": n,
    }


def match_rows(
    rows: Iterable[CanonicalRow],
    db: dict,
    fab_mapping: dict[str, str],
) -> list[MatchResult]:
    """Match each CanonicalRow against the ProCos database.

    Status values produced:
        "MATCH"                       -- unique hit on (fab, type)
        "NIET UNIEK"                  -- >1 hit on (fab, type)
        "NIET GEVONDEN"               -- no hit but fab was mapped
        "MATCH (op type alleen)"      -- unique hit on type-only (fab not mapped)
        "NIET UNIEK (op type alleen)" -- >1 hit on type-only (fab not mapped)
        "NIET GEVONDEN (fab niet gemapt)" -- no hit, fab not mapped
        "GEEN TYPE NR"                -- row had no usable type-nr candidates

    Args:
        rows: list of CanonicalRow objects (from extractor.pipeline)
        db: result of load_procos_db()
        fab_mapping: dict mapping uppercase PDF-fabrikant-tekst to ProCos
                     fabcode, e.g. {"SIEMENS": "SIEHAA", "EATON": "EATVEJ"}.

    Returns:
        List of MatchResult, in the same order as the input rows.
    """
    by_fab_type = db["by_fab_type"]
    by_type_only = db["by_type_only"]

    results: list[MatchResult] = []
    for row in rows:
        results.append(_match_one(row, by_fab_type, by_type_only, fab_mapping))
    return results


def _match_one(
    row: CanonicalRow,
    by_fab_type: dict[tuple[str, str], list[dict]],
    by_type_only: dict[str, list[dict]],
    fab_mapping: dict[str, str],
) -> MatchResult:
    candidates = _candidate_typenrs(row)
    if not candidates:
        return MatchResult(status="GEEN TYPE NR")

    fab_raw = (row.manufacturer or "").strip().upper()
    fab_code = fab_mapping.get(fab_raw, "")

    last_norm = ""
    for cand in candidates:
        norm = _norm_type(cand)
        if not norm:
            continue
        last_norm = norm

        if fab_code:
            hits = by_fab_type.get((fab_code, norm), [])
            if len(hits) == 1:
                h = hits[0]
                return MatchResult(
                    status="MATCH",
                    procos_artikel=h["artikel"],
                    procos_fabcode=h["fabrikant"],
                    procos_omschrijving=h["omschrijving"],
                    mapped_fab=fab_code,
                    matched_typenr=cand,
                    n_hits=1,
                )
            if len(hits) > 1:
                h = hits[0]
                return MatchResult(
                    status="NIET UNIEK",
                    procos_artikel=h["artikel"],
                    procos_fabcode=h["fabrikant"],
                    procos_omschrijving=h["omschrijving"],
                    mapped_fab=fab_code,
                    matched_typenr=cand,
                    n_hits=len(hits),
                )
        else:
            hits = by_type_only.get(norm, [])
            if len(hits) == 1:
                h = hits[0]
                return MatchResult(
                    status="MATCH (op type alleen)",
                    procos_artikel=h["artikel"],
                    procos_fabcode=h["fabrikant"],
                    procos_omschrijving=h["omschrijving"],
                    mapped_fab="",
                    matched_typenr=cand,
                    n_hits=1,
                )
            if len(hits) > 1:
                h = hits[0]
                return MatchResult(
                    status="NIET UNIEK (op type alleen)",
                    procos_artikel=h["artikel"],
                    procos_fabcode=h["fabrikant"],
                    procos_omschrijving=h["omschrijving"],
                    mapped_fab="",
                    matched_typenr=cand,
                    n_hits=len(hits),
                )

    return MatchResult(
        status="NIET GEVONDEN" if fab_code else "NIET GEVONDEN (fab niet gemapt)",
        mapped_fab=fab_code,
        matched_typenr=last_norm,
        n_hits=0,
    )


def summarize(results: list[MatchResult]) -> dict:
    """Aggregate stats over a list of MatchResult.

    Returns:
        {
            "total":       int,
            "by_status":   {status_string: count, ...},
            "match_count": int (any MATCH variant),
            "match_pct":   float (0..100, 1 decimal)
        }
    """
    total = len(results)
    by_status: dict[str, int] = defaultdict(int)
    match_count = 0
    for r in results:
        by_status[r.status] += 1
        if r.status.startswith("MATCH"):
            match_count += 1

    pct = (100.0 * match_count / total) if total else 0.0
    return {
        "total": total,
        "by_status": dict(by_status),
        "match_count": match_count,
        "match_pct": round(pct, 1),
    }
