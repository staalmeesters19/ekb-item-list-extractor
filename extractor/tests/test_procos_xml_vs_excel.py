"""Cross-check our Python ProCos XML writer against Excel's own SaveAsXMLData
output for 10 test cases.

For every case:
  1. Build a list of klantlijst rows (the 10 cells A..J per row).
  2. Generate XML via Python (render_xml on equivalent CanonicalRow objects).
  3. Generate XML via Excel COM (fill template, recalc, copy Daten -> XML
     Ausgabe, call XmlMap.Export).
  4. Parse both and compare every <P> element's attributes.

Run from repo root:
    python -m extractor.tests.test_procos_xml_vs_excel
or:
    python extractor/tests/test_procos_xml_vs_excel.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

_HERE = Path(__file__).resolve().parent
_EXTRACTOR_ROOT = _HERE.parent
if str(_EXTRACTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTRACTOR_ROOT))

from src.interfaces import CanonicalRow, ExtractionResult  # noqa: E402
from src.writers.procos_xml_writer import render_xml  # noqa: E402

_TEMPLATE = _EXTRACTOR_ROOT / "src" / "writers" / "templates" / "ProCosImportStuklijst.xltm"


# ---------------------------------------------------------------------------
# Test cases — list of "rows", each row is a 10-tuple (A..J of klantlijst).
# A=Aantal, B=Eenheid, C=Klantartikel, D=Omschrijving, E=Fabrikant,
# F=Type/bestelnr, G=toegeleverd, H=ODC, I=Opmerking, J=EAN
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "name": "01_simple_single_row",
        "rows": [
            (1, "ST", "100F0", "CIRCUIT-BREAKER, 2P, 4A", "EATON", "FAZ-C4/2", "", "", "", ""),
        ],
    },
    {
        "name": "02_three_rows",
        "rows": [
            (1, "ST", "100F0", "First row",  "EATON",   "FAZ-C4/2",         "", "", "", ""),
            (2, "ST", "100Q0", "Second row", "ITSME",   "T0-1-102/V/SVB",   "", "", "", ""),
            (1, "ST", "21K1",  "Third row",  "SIEMENS", "6ED1052-1MD08-0BA1","", "", "", ""),
        ],
    },
    {
        "name": "03_ampersand_and_lt_gt",
        "rows": [
            (1, "ST", "TAGX", "AT&T device <special> v2", "ACME", "MODEL-X", "", "", "", ""),
        ],
    },
    {
        "name": "04_double_quotes_in_text",
        "rows": [
            (1, "ST", "TAGY", 'Item with "quoted" text', "ACME", "M-1", "", "", "", ""),
        ],
    },
    {
        "name": "05_empty_klantartikel_fallback",
        "rows": [
            # C empty -> KndArtikel must fall back to E&F (manufacturer + type)
            (1, "ST", "", "Item without tag", "EATON", "FAZ-C4/2", "", "", "", ""),
        ],
    },
    {
        "name": "06_long_description_truncate_120",
        "rows": [
            (1, "ST", "TAG3", "X" * 200, "ACME", "M", "", "", "", ""),
        ],
    },
    {
        "name": "07_unicode_chars",
        "rows": [
            (1, "ST", "TAG4", "Spülmaschine ßéü Ω", "ACME", "M", "", "", "", ""),
        ],
    },
    {
        "name": "08_bemerkung_only",
        # ODC (col H) is intentionally empty — our extractor never produces it
        # (not present in source PDFs); BMK stays "" in our writer by design.
        "rows": [
            (1, "ST", "TAG5", "Item with remark", "ACME", "M-99", "", "", "Belangrijk!", ""),
        ],
    },
    {
        "name": "09_decimal_quantity",
        "rows": [
            (1.5, "ST", "TAG6", "Half item", "ACME", "M", "", "", "", ""),
        ],
    },
    {
        "name": "10_mixed_5_rows",
        "rows": [
            (1,   "ST", "100F0", "Circuit breaker",        "EATON",   "FAZ-C4/2",  "", "", "", ""),
            (3,   "ST", "100Q0", "Switch w/ \"contacts\"", "ITSME",   "T0-1",      "", "", "", ""),
            (1,   "ST", "",      "Mounting bracket",       "EATON",   "ZAV-T0",    "", "", "", ""),
            (1,   "ST", "21K1",  "Logo controller é & ü",  "SIEMENS", "6ED1052",   "", "", "Sectie A", ""),
            (2.5, "ST", "TAG-Z", "Cable 2.5m",             "LAPP",    "OLFLEX",    "", "", "", ""),
        ],
    },
]


# ---------------------------------------------------------------------------
# Build CanonicalRow objects for the Python writer
# ---------------------------------------------------------------------------

def _row_tuple_to_canonical(t: tuple, row_index: int) -> CanonicalRow:
    aantal, eenheid, klant, omsch, fab, type_nr, toegeleverd, odc, opmerking, ean = t
    warnings = [opmerking] if opmerking else []
    return CanonicalRow(
        source_pdf="test",
        source_page=1,
        source_section=None,
        row_index=row_index,
        device_tag=(klant or None),
        quantity=aantal,
        description=omsch,
        manufacturer=fab,
        model_number=type_nr,
        order_number=None,
        schematic_position=None,
        extra_fields={},
        raw=[],
        warnings=warnings,
    )


def _make_extraction_result(rows: list[tuple]) -> ExtractionResult:
    canonical = [_row_tuple_to_canonical(r, i) for i, r in enumerate(rows)]
    return ExtractionResult(source_pdf="test", rows=canonical, audit={})


# ---------------------------------------------------------------------------
# Excel COM helpers
# ---------------------------------------------------------------------------

def _excel_export(excel, rows: list[tuple], xml_out: str) -> None:
    """Open template fresh, fill klantlijst, recalc, copy to XML Ausgabe, export XML."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xltm", delete=False)
    tmp.close()
    shutil.copy2(_TEMPLATE, tmp.name)
    if os.path.exists(xml_out):
        os.remove(xml_out)
    try:
        wb = excel.Workbooks.Open(tmp.name)
        ws = wb.Worksheets("klantlijst")
        for i, row in enumerate(rows, start=2):
            for c, val in enumerate(row, start=1):
                ws.Cells(i, c).Value = val if val != "" else None
        excel.CalculateFull()

        wsD = wb.Worksheets("Daten")
        wsX = wb.Worksheets("XML Ausgabe")
        last = len(rows) + 1
        src_range = wsD.Range(wsD.Cells(2, 1), wsD.Cells(last, 15))
        dst_range = wsX.Range(wsX.Cells(2, 1), wsX.Cells(last, 15))
        dst_range.Value = src_range.Value

        m = wb.XmlMaps("Stueckliste_toewijzing")
        rc = m.Export(xml_out)
        if rc != 0:
            raise RuntimeError(f"XmlMap.Export returned {rc} (non-zero)")
        wb.Close(False)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _parse_p_attrs(xml_text: str) -> list[dict]:
    """Parse <Stueckliste><P .../>...</Stueckliste> into list-of-dicts."""
    root = ET.fromstring(xml_text)
    return [dict(p.attrib) for p in root.findall("P")]


def _diff_rows(py_attrs: list[dict], xl_attrs: list[dict]) -> list[str]:
    """Return list of human-readable diffs (empty list = perfect match)."""
    diffs: list[str] = []
    if len(py_attrs) != len(xl_attrs):
        diffs.append(f"row count differs: python={len(py_attrs)} excel={len(xl_attrs)}")
        return diffs
    for i, (py, xl) in enumerate(zip(py_attrs, xl_attrs), start=1):
        py_keys = set(py.keys())
        xl_keys = set(xl.keys())
        if py_keys != xl_keys:
            only_py = py_keys - xl_keys
            only_xl = xl_keys - py_keys
            if only_py:
                diffs.append(f"row {i}: keys only in python: {only_py}")
            if only_xl:
                diffs.append(f"row {i}: keys only in excel:  {only_xl}")
        for k in sorted(py_keys & xl_keys):
            if py[k] != xl[k]:
                diffs.append(
                    f"row {i} attr {k!r}: python={py[k]!r}  excel={xl[k]!r}"
                )
    return diffs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    if not _TEMPLATE.exists():
        print(f"FATAL: template not found at {_TEMPLATE}", file=sys.stderr)
        return 2

    try:
        import win32com.client as w32
    except ImportError:
        print("FATAL: pywin32 not installed (pip install pywin32)", file=sys.stderr)
        return 2

    excel = w32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False

    out_dir = Path(tempfile.gettempdir()) / "ekb_xml_compare"
    out_dir.mkdir(exist_ok=True)

    passed = 0
    failed = 0
    failures: list[tuple[str, list[str]]] = []

    try:
        for case in TEST_CASES:
            name = case["name"]
            rows = case["rows"]

            # Python output
            result = _make_extraction_result(rows)
            py_xml = render_xml(result)
            py_path = out_dir / f"{name}_python.xml"
            py_path.write_text(py_xml, encoding="utf-8")

            # Excel output
            xl_path = out_dir / f"{name}_excel.xml"
            _excel_export(excel, rows, str(xl_path))
            xl_xml = xl_path.read_text(encoding="utf-8")

            # Compare semantically
            py_attrs = _parse_p_attrs(py_xml)
            xl_attrs = _parse_p_attrs(xl_xml)
            diffs = _diff_rows(py_attrs, xl_attrs)

            if not diffs:
                passed += 1
                print(f"  [PASS] {name}  ({len(rows)} rows)")
            else:
                failed += 1
                failures.append((name, diffs))
                print(f"  [FAIL] {name}  ({len(diffs)} diffs)")
                for d in diffs[:5]:
                    print(f"          - {d}")
                if len(diffs) > 5:
                    print(f"          ... (+{len(diffs)-5} more)")
    finally:
        excel.Quit()

    print()
    print(f"=== {passed}/{passed+failed} tests passed ===")
    if failures:
        print(f"Files saved to: {out_dir}")
        return 1
    print(f"Output files: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
