# EKB Item-list Extractor

Automatische stuklijst-extractie uit elektrische tekeningen (PDF). Je gooit er
een tekening in, de tool vindt de pagina's met de stuklijst, haalt elke rij
exact eruit en exporteert ze als Excel, CSV of JSON.

Drie samenwerkende delen:

| Deel | Rol | Status |
|---|---|---|
| **Classifier** | Vindt welke pagina's een stuklijst bevatten | ✅ 4/4 tests groen · 11/11 runs gevonden, 0 false positives |
| **Extractor** | Haalt elke rij structureel uit de gevonden pagina's | ✅ 8/8 tests groen · 100–108% van de verwachte rijen per PDF |
| **Frontend** | Streamlit-UI voor upload, preview en download | ✅ Draait lokaal op poort 8501 |

**PDF-agnostisch** — geen EPLAN/Siemens-specifieke logica. Nieuwe PDF-stijlen
werken zonder codewijzigingen; alleen het synoniemen-woordenboek in
`extractor/config.yaml` breidt soms uit.

---

## Architectuur

```
PDF in
  │
  ▼
┌───────────────────────────────────────────────┐
│  CLASSIFIER  (classifier/)                    │
│  5 gewogen signalen per pagina:               │
│    column_header · vector_density · row_count │
│    title · continuity                         │
│  → cluster naar "ItemListRuns"                │
│  → List[List[int]]  (pagina-ranges)           │
└───────────────────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────────────────┐
│  EXTRACTOR  (extractor/)                      │
│  per pagina:                                  │
│    table_extractor  (PyMuPDF + pdfplumber)    │
│    header-row promotion                       │
│    table_selector   (4-11 kolommen, qty req.) │
│    column_mapper    (fuzzy synonymen)         │
│    row_parser       (section-header erven)    │
│    post_processor   (qty parsen, unicode)     │
│    section_detector (run-diff labels)         │
│    validator        (cross-parser consensus)  │
│  → ExtractionResult (List[CanonicalRow])      │
└───────────────────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────────────────┐
│  FRONTEND  (frontend/)                        │
│  Streamlit: upload → processing → results     │
│  Writers: XLSX / CSV / JSON                   │
└───────────────────────────────────────────────┘
  │
  ▼
Download
```

---

## Mappenstructuur

```
Item list poc/
├── README.md                         ← dit document
├── .gitignore
│
├── data/                             ← 5 test-PDFs (klantvertrouwelijk, niet in git)
│
├── classifier/                       ← Fase 1
│   ├── cli.py                        ← python classifier/cli.py <pdf>
│   ├── config.yaml                   ← signaalgewichten, drempelwaarden
│   ├── ground_truth.yaml             ← handgeverifieerde paginarange per PDF
│   ├── requirements.txt
│   ├── conftest.py
│   ├── src/
│   │   ├── classifier.py             ← top-level orchestrator (classify())
│   │   ├── scorer.py                 ← twee-pass scoring
│   │   ├── clusterer.py              ← groepeer aansluitende hoge-score pagina's
│   │   ├── interfaces.py             ← SignalResult, PageScore, ItemListRun
│   │   └── signals/
│   │       ├── column_header.py      ← strongste signaal (gewicht 10)
│   │       ├── vector_density.py     ← pagina-complexiteit (stille pagina's = lijsten)
│   │       ├── row_count.py
│   │       ├── title.py
│   │       └── continuity.py         ← post-pass: buur-bonus
│   └── tests/test_classifier.py      ← 4 tests
│
├── extractor/                        ← Fase 2
│   ├── cli.py                        ← python extractor/cli.py <pdf> [--format xlsx]
│   ├── config.yaml                   ← synoniem-dictionary, validatie-drempels
│   ├── requirements.txt
│   ├── conftest.py
│   ├── src/
│   │   ├── interfaces.py             ← RawTable, ColumnMapping, CanonicalRow, ExtractionResult
│   │   ├── pipeline.py               ← orchestrator (run())
│   │   ├── table_extractor.py        ← PyMuPDF + pdfplumber, beide parsers
│   │   ├── table_selector.py         ← filter op kolomaantal + quantity-header
│   │   ├── column_mapper.py          ← fuzzy Levenshtein → canonieke velden
│   │   ├── row_parser.py             ← rij-parsing, section-header erven
│   │   ├── section_detector.py       ← run-diff heuristiek voor sectielabels
│   │   ├── post_processor.py         ← quantity parsen, unicode schoonmaken, regex
│   │   ├── validator.py              ← cross-parser consensus, required fields
│   │   └── writers/
│   │       ├── csv_writer.py
│   │       ├── xlsx_writer.py        ← sheet-per-PDF optie
│   │       └── json_writer.py
│   └── tests/test_pipeline.py        ← 8 integratie-tests
│
└── frontend/                         ← Fase 3 (Streamlit)
    ├── app.py                        ← streamlit run app.py
    ├── requirements.txt
    ├── BRAND_BRIEF.md                ← research output: EKB kleuren + SaaS principes
    ├── .streamlit/config.toml        ← thema (EKB blauw #003478)
    ├── assets/custom.css             ← 244 regels polish
    ├── backend/
    │   ├── pipeline_service.py       ← wrapper rond classifier + extractor
    │   └── test_service.py           ← sanity-test
    └── components/
        ├── upload.py                 ← scherm 1: drag-drop uploader
        ├── processing.py             ← scherm 2: live st.status per bestand
        └── results.py                ← scherm 3: metrics, filters, tabel, downloads
```

---

## Installatie

**Python 3.11 of hoger.**

```bash
# Per deel een eigen requirements (of installeer alle drie tegelijk)
pip install -r classifier/requirements.txt
pip install -r extractor/requirements.txt
pip install -r frontend/requirements.txt
```

Dependencies (niet exhaustief): `streamlit`, `pandas`, `pymupdf`, `pdfplumber`,
`rapidfuzz`, `openpyxl`, `pyyaml`.

---

## Gebruik

### 1 — Frontend (aanbevolen)

```bash
cd frontend
streamlit run app.py
```

Open **http://localhost:8501**. Drag-drop één of meerdere PDFs →
verwerking → preview tabel → download XLSX/CSV/JSON.

### 2 — Command-line (classifier alleen)

```bash
python classifier/cli.py pad/naar/tekening.pdf
# → print gedetecteerde paginaranges
```

### 3 — Command-line (extractor stand-alone)

```bash
python extractor/cli.py pad/naar/tekening.pdf --format xlsx --output ./out
# handmatige paginaselectie:
python extractor/cli.py pad/naar/tekening.pdf --pages "33-35,47-51" --format csv
```

---

## Tests draaien

```bash
# Classifier (duurt ~2 min — draait tegen alle 5 PDFs in data/)
cd classifier && python -m pytest tests/ -v

# Extractor (duurt ~1.5 min)
cd extractor && python -m pytest tests/ -v

# Backend-service smoke-test (duurt ~20s)
python frontend/backend/test_service.py
```

Tests verwachten dat de 5 test-PDFs in `data/` staan. De bestanden staan
niet in git (klantvertrouwelijk); zet ze lokaal op de juiste plek voor je
test runt.

---

## Testresultaten (per PDF, laatste run)

| PDF | Pagina's | Detected runs | Rijen extracted | Verwacht |
|---|---|---|---|---|
| 126-0053 Cabinet Lineator Controller | 37 | 1 (p.33-35) | **76** | ≈76 (100%) |
| 9263111 ILCU REV-V1.0 | 13 | 1 (p.10-11) | **51** | ≈51 (100%) |
| G88000 Network Cabinets | 247 | 4 | **319** | ≈295 (108%) |
| MAXXeGUARD Beckhoff V4.26 | 37 | 3 | **174** | ≈174 (100%) |
| NGB-NGQ V4.0 | 60 | 2 | **348** | ≈340 (102%) |

---

## Belangrijkste ontwerpkeuzes

### Classifier: 5 signalen, niet AI
Eerst gebouwd als signaal-gebaseerd systeem omdat het voorspelbaar is en
geen trainingsdata nodig heeft. Werkt door 5 onafhankelijke heuristieken
per pagina te wegen (column headers, vector dichtheid, row count, title,
continuïteit met buren). Drempelwaarden zijn gekalibreerd uit de 5 test-PDFs
maar niet per-PDF getuned — zelfde gewichten werken PDF-breed.

### Extractor: twee parsers voor consensus
PyMuPDF is de primaire parser (behoudt spaties correct), pdfplumber loopt
parallel mee. Als de rij-aantallen > 1 verschillen komt er een
`consensus_warning` in de audit. Beide parsers kunnen header-promotion nodig
hebben: als de tabel-"header" een tekening-titel blijkt (bv. "EY0X-SSSA"),
promoveert pipeline de eerste datarij naar kolomkop.

### PDF-agnostisch
Geen hardcoded profielen per PDF-stijl. Kolommen worden semantisch
herkend via een synoniemen-dictionary + fuzzy match (Levenshtein ≤ 2 voor
strings ≥ 4 tekens). Onbekende kolommen gaan in `extra_fields` zodat er
nooit data verloren gaat.

### Section-label detectie (run-diff heuristiek)
Voor multi-run PDFs (bv. G88000 met 4 aparte stuklijsten per kast)
extraheert de `section_detector` tokens uit de pagina-titelblok-zones, en
zoekt tokens die stabiel zijn binnen één run maar veranderen tussen runs.
Zo herkent hij automatisch "+K1", "+BASIC", enz. zonder de EPLAN-syntax te
kennen.

### Frontend: Streamlit
Bewust gekozen boven React/Next.js voor snelheid naar werkende POC. Eén
Python-codebase voor UI + backend. Deploybaar op Streamlit Community Cloud
in < 15 min. Als de tool productie-klaar moet worden voor meerdere
gebruikers tegelijk: splitsen naar FastAPI-backend + aparte frontend.

---

## Bekende beperkingen

- **Classifier-snelheid**: ~10–20s per PDF (PyMuPDF + pdfplumber op elke pagina).
  Voor een PDF van 247 pagina's is dat ~1 minuut.
- **Extractor-snelheid**: ~20–40s voor een batch van 5-20 stuklijst-pagina's.
- **Frontend**: single-user, geen auth, geen job-queue. Upload-limiet 200MB.
- **Tabel-detectie**: hangt af van PyMuPDF/pdfplumber. Tekeningen met
  zeer ongewone celstructuur (bv. overlappende tabellen of
  gescande/niet-tekstuele PDFs) vereisen OCR — niet geïmplementeerd.
- **Klant-PDFs niet in git**: zet ze in `data/` na clone; tests verwachten
  de bestandsnamen uit `classifier/ground_truth.yaml`.

---

## Deploy naar Streamlit Community Cloud

1. Push dit repo naar GitHub (publiek of privé).
2. Ga naar https://streamlit.io/cloud → New app → selecteer repo.
3. Main file path: `frontend/app.py`
4. Python version: 3.11
5. Secrets: geen nodig.
6. De PDFs in `data/` zijn NIET in git (per `.gitignore`). Voor een
   gedeployde demo: of voeg een dummy-PDF toe, of laat gebruikers hun eigen
   PDF uploaden (dat is de normale flow).

---

## Contact & achtergrond

Initieel gebouwd voor EKB (divisie van Agyle) om elektrische-tekening
stuklijsten automatisch te verwerken. PDF-agnostische opzet zodat de tool
ook voor toekomstige klanten en toolchains werkt zonder aanpassingen per
leverancier.
