# Item List Classifier

Regelgebaseerde Python classifier die in een elektrotechnische tekening-PDF bepaalt
op welke pagina's de item list (parts list / BOM / stuklijst) staat.

## Architectuur

Pipeline van 3 lagen:

1. **Signalen** (`src/signals/`) - per pagina, onafhankelijk:
   - `title.py`         - Signaal 1: titel-regex
   - `column_header.py` - Signaal 2: kolomkoppen-fingerprint (belangrijkste)
   - `row_count.py`     - Signaal 3: aantal data-rijen
   - `vector_density.py`- Signaal 4: pagina-"rustheid"
   - `continuity.py`    - Signaal 5: buur-continuiteit (post-scoring)
2. **Scorer** (`src/scorer.py`) - gewogen som van alle signalen per pagina.
3. **Clusterer** (`src/clusterer.py`) - aangrenzende matches -> item-list runs.

## Software-agnostisch

Alle signalen gebruiken structurele eigenschappen van item-lists zelf,
geen aannames over specifieke CAD-software (EPLAN, Siemens, AutoCAD etc.).

## Gebruik

```
cd classifier
python -m cli <pdf-path>
# of
python -m pytest tests/
```

## Config

`config.yaml` bevat alle tunbare parameters: gewichten, drempels, synoniemen.

## Ground truth

`ground_truth.yaml` bevat handmatig geverifieerde pagina-ranges voor de 5 test-PDFs.
