"""ProCos XML writer — direct generation, no Excel middleman.

Produces the same XML that Excel's "XML Opslaan" macro produces from the
embedded XML Map (``Stueckliste_toewijzing``) in the .xltm template.
Eliminates the entire Excel round-trip: faster, no macro permission
prompts, no Excel installation needed, works on Streamlit Cloud.

Schema (15 required attributes per ``<P>`` element, fixed order):

    POS, Menge, ME, KndBezeichnung, KndArtikel, BstNr, Lieferant, Typ,
    Hersteller, BMK, Bemerkung, SeitePfad, Beistellung, EinbauOrt, EANNR

Static values (from template formulas in Daten sheet):
    Typ        = "zie bstnr"
    SeitePfad  = "not used"
    EinbauOrt  = "not used"
    Beistellung= "0"  (always — `toegeleverd` is left empty in our writer)
    BMK        = ""   (ODC code not present in source PDFs)
    EANNR      = ""   (EAN not present in source PDFs)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape

from ..interfaces import CanonicalRow, ExtractionResult


# Fixed 15-attribute order (matches Excel's SaveAsXMLData output).
_ATTRIBUTES = (
    "POS", "Menge", "ME", "KndBezeichnung", "KndArtikel",
    "BstNr", "Lieferant", "Typ", "Hersteller", "BMK",
    "Bemerkung", "SeitePfad", "Beistellung", "EinbauOrt", "EANNR",
)

_KND_BEZEICHNUNG_MAX = 120  # Daten formula does LEFT(D, 120)


def _quantity_to_str(raw: Any) -> str:
    """Render quantity as Excel would render it: int when whole, else str."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, bool):
        return str(int(raw))
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        return str(int(raw)) if raw.is_integer() else str(raw)
    return str(raw)


def _bestelnr(row: CanonicalRow) -> str:
    """Type/bestelnummer = model_number, fallback order_number."""
    primary = (row.model_number or "").strip()
    if primary:
        return primary
    return (row.order_number or "").strip()


def _knd_artikel(row: CanonicalRow, bstnr: str) -> str:
    """Klantartikel: device_tag if present, else manufacturer+bstnr (Excel fallback)."""
    tag = (row.device_tag or "").strip()
    if tag:
        return tag
    fab = (row.manufacturer or "").strip()
    return fab + bstnr


def _bemerkung(row: CanonicalRow) -> str:
    parts: list[str] = []
    if row.source_section:
        parts.append(f"[{row.source_section}]")
    if row.warnings:
        parts.append("; ".join(row.warnings))
    return " ".join(parts)


def _escape_attr(s: str) -> str:
    """Escape for XML attribute (always wrapped in double quotes)."""
    return escape(s, {'"': "&quot;"})


def _row_to_attrs(pos: int, row: CanonicalRow) -> dict[str, str]:
    bstnr = _bestelnr(row)
    desc = (row.description or "").strip()
    if len(desc) > _KND_BEZEICHNUNG_MAX:
        desc = desc[:_KND_BEZEICHNUNG_MAX]
    manufacturer = (row.manufacturer or "").strip()
    return {
        "POS":            str(pos),
        "Menge":          _quantity_to_str(row.quantity),
        "ME":             "ST",
        "KndBezeichnung": desc,
        "KndArtikel":     _knd_artikel(row, bstnr),
        "BstNr":          bstnr,
        "Lieferant":      manufacturer,
        "Typ":            "zie bstnr",
        "Hersteller":     manufacturer,
        "BMK":            "",
        "Bemerkung":      _bemerkung(row),
        "SeitePfad":      "not used",
        "Beistellung":    "0",
        "EinbauOrt":      "not used",
        "EANNR":          "",
    }


def render_xml(result: ExtractionResult) -> str:
    """Build the XML string. Useful for unit tests / byte-comparison."""
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             "<Stueckliste>"]
    for i, row in enumerate(result.rows, start=1):
        attrs = _row_to_attrs(i, row)
        attr_strs = " ".join(f'{k}="{_escape_attr(attrs[k])}"' for k in _ATTRIBUTES)
        lines.append(f"\t<P {attr_strs}/>")
    lines.append("</Stueckliste>")
    return "\n".join(lines)


def write_procos_xml(
    result: ExtractionResult,
    output_path: str,
    config: Optional[dict] = None,
) -> None:
    """Write the ProCos XML to *output_path*."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_xml(result), encoding="utf-8")
