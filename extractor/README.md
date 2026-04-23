# Item List Extractor

Generieke extractor die item-list rijen uit elektrotechnische tekening-PDFs haalt.
Werkt voor elke PDF die native tekst bevat, zonder per-PDF configuratie.

## Principe

- **Geen layout-profielen**: kolommen worden per PDF semantisch gedetecteerd via
  een gedeeld synoniemen-woordenboek + fuzzy matching.
- **Onbekende kolommen verdwijnen niet**: alles wat niet mapt op een canonical veld
  gaat in `extra_fields`.
- **Cross-parser consensus**: PyMuPDF primair, pdfplumber als kwaliteits-check.
- **Generieke edge cases**: section-header inheritance, multi-line cells,
  trailing padding worden structureel herkend, niet per PDF.

## Pipeline

1. Classifier (peer-project) vindt item-list pagina's.
2. TableSelector pakt de echte data-tabel per pagina (niet het tekeningkader).
3. TableExtractor leest met PyMuPDF + pdfplumber, consensus-check.
4. ColumnMapper detecteert per kolom de canonical category.
5. RowParser filtert padding, detecteert section-headers, erft labels.
6. SectionDetector detecteert sub-section labels per run.
7. PostProcessor normaliseert (quantity parsen, encoding, regex extracts).
8. Validator: self-validation (consensus + structural checks).
9. Writer: CSV / XLSX / JSON output.

## Gebruik

```bash
cd extractor
python -m cli <pdf-path> [--format csv|xlsx|json] [--output <dir>]
pytest tests/
```
