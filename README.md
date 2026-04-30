# EKB Item-list Extractor

Automatische stuklijst-extractie uit elektrische tekeningen (PDF). Je gooit er
een tekening in, de tool vindt de pagina's met de stuklijst, haalt elke rij
exact eruit en levert een **`.xltm`-bestand voor ProCos** terug — klaar voor
EKB's bestaande "XML Opslaan → import" flow.

| Deel | Rol | Status |
|---|---|---|
| **Classifier** | Vindt welke pagina's een stuklijst bevatten | ✅ 4/4 tests groen · 11/11 runs gevonden, 0 false positives |
| **Extractor** | Haalt elke rij structureel uit de gevonden pagina's | ✅ 8/8 tests groen · 100–108% van de verwachte rijen per PDF |
| **ProCos-export** | Vult EKB's `klantlijst`-template; VBA-macro's blijven intact | ✅ 5/5 PDFs gevalideerd · vbaProject.bin behouden |
| **Frontend** | Streamlit-UI: upload → preview → download `.xltm` | ✅ Lokaal + Streamlit Cloud public deploy |

**PDF-agnostisch** — geen EPLAN/Siemens-specifieke logica. Nieuwe PDF-stijlen
werken zonder codewijzigingen; alleen het synoniemen-woordenboek in
`extractor/config.yaml` breidt soms uit.

---

## Live deploys & repo

| Onderdeel | URL |
|---|---|
| GitHub repo | https://github.com/staalmeesters19/ekb-item-list-extractor (public) |
| Streamlit Cloud app | `ekb-item-list-extractor-*.streamlit.app` (zie share.streamlit.io → Manage app) |
| Lokale dev | http://localhost:8501 |

GitHub-account: **`staalmeesters19`**. Streamlit Cloud auto-deployt bij elke
push naar `main` (1–3 min build-tijd). Beheer en logs via
https://share.streamlit.io.

Lokale projectmap: `C:\Users\JorisMerkx\OneDrive - Agyle\Documenten\EKB\Item list poc`
(OneDrive-synced).

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
│    header-row promotion (BOM-pagina's)        │
│    table_selector   (4-11 kolommen, qty req.) │
│    column_mapper    (fuzzy synonymen)         │
│    + device_tag positional fallback           │
│    row_parser       (section-header erven)    │
│    post_processor   (qty parsen, unicode)     │
│    section_detector (run-diff labels)         │
│    validator        (cross-parser consensus)  │
│  → ExtractionResult (List[CanonicalRow])      │
└───────────────────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────────────────┐
│  WRITERS  (extractor/src/writers/)            │
│    procos_writer  ← primaire output (.xltm)   │
│    xlsx_writer    (sheet-per-PDF)             │
│    csv_writer                                 │
│    json_writer                                │
└───────────────────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────────────────┐
│  FRONTEND  (frontend/)                        │
│  Streamlit: upload → processing → results     │
│  Eén centrale knop: "Download de ProCos"      │
└───────────────────────────────────────────────┘
  │
  ▼
EKB opent .xltm  →  klikt XML Opslaan  →  ProCos importeert
```

---

## Mappenstructuur

```
Item list poc/
├── README.md                         ← dit document
├── .gitignore                        ← sluit data/ PDFs en root /*.xltm /*.doc uit
│
├── data/                             ← 5 test-PDFs (klantvertrouwelijk, niet in git)
│
├── classifier/                       ← Fase 1
│   ├── cli.py
│   ├── config.yaml                   ← signaalgewichten, drempelwaarden
│   ├── ground_truth.yaml             ← handgeverifieerde paginarange per PDF
│   ├── requirements.txt
│   ├── conftest.py
│   ├── src/
│   │   ├── classifier.py             ← classify(pdf_path) → (runs, scores)
│   │   ├── scorer.py                 ← twee-pass scoring
│   │   ├── clusterer.py              ← groepeer aansluitende hoge-score pagina's
│   │   ├── interfaces.py             ← SignalResult, PageScore, ItemListRun
│   │   └── signals/
│   │       ├── column_header.py      ← strongste signaal (gewicht 10)
│   │       ├── vector_density.py     ← pagina-complexiteit
│   │       ├── row_count.py
│   │       ├── title.py
│   │       └── continuity.py         ← post-pass: buur-bonus
│   └── tests/test_classifier.py      ← 4 tests
│
├── extractor/                        ← Fase 2
│   ├── cli.py                        ← python extractor/cli.py <pdf> --format procos
│   ├── config.yaml                   ← synoniem-dictionary, validatie-drempels
│   ├── requirements.txt
│   ├── conftest.py
│   ├── src/
│   │   ├── interfaces.py             ← RawTable, ColumnMapping, CanonicalRow, ExtractionResult
│   │   ├── pipeline.py               ← orchestrator (run()) + device_tag fallback
│   │   ├── table_extractor.py        ← PyMuPDF + pdfplumber, beide parsers
│   │   ├── table_selector.py         ← filter op kolomaantal + quantity-header
│   │   ├── column_mapper.py          ← fuzzy Levenshtein → canonieke velden
│   │   ├── row_parser.py             ← rij-parsing, section-header erven
│   │   ├── section_detector.py       ← run-diff heuristiek voor sectielabels
│   │   ├── post_processor.py         ← quantity parsen, unicode schoonmaken, regex
│   │   ├── validator.py              ← cross-parser consensus, required fields
│   │   └── writers/
│   │       ├── csv_writer.py
│   │       ├── xlsx_writer.py
│   │       ├── json_writer.py
│   │       ├── procos_writer.py      ← primair: vult EKB's klantlijst-template
│   │       └── templates/
│   │           └── ProCosImportStuklijst.xltm  ← gebundeld
│   └── tests/test_pipeline.py        ← 8 integratie-tests
│
└── frontend/                         ← Fase 3 (Streamlit)
    ├── app.py                        ← entry point: streamlit run app.py
    ├── requirements.txt
    ├── BRAND_BRIEF.md                ← research: EKB kleuren + SaaS principes
    ├── .streamlit/config.toml        ← thema (EKB blauw #003478)
    ├── assets/custom.css             ← polish (knoppen, kaartjes, typografie)
    ├── backend/
    │   ├── pipeline_service.py       ← wrapper rond classifier + extractor
    │   └── test_service.py           ← sanity-test (incl. ProCos bytes)
    └── components/
        ├── upload.py                 ← scherm 1: drag-drop uploader
        ├── processing.py             ← scherm 2: live st.status per bestand
        └── results.py                ← scherm 3: metrics, filters, tabel, ProCos download
```

---

## Snel starten (lokaal)

**Python 3.11 of hoger.** Op deze machine staan alle dependencies al
geïnstalleerd (Python 3.13.12 + streamlit 1.56 + pandas 3.0.2 + pymupdf +
pdfplumber + openpyxl + rapidfuzz + pyyaml).

Voor een verse setup:

```bash
pip install -r classifier/requirements.txt
pip install -r extractor/requirements.txt
pip install -r frontend/requirements.txt
```

**Frontend starten**:

```bash
cd frontend
streamlit run app.py
# → http://localhost:8501
```

Eerste keer prompt Streamlit om een email; wij hebben dat omzeild met
een lege `~/.streamlit/credentials.toml`. Niet aanpassen.

---

## ProCos-export — het eindproduct

Doel: EKB krijgt een `<pdf-naam>_procos.xltm` terug die ze direct in hun
bestaande ProCos-flow kunnen gebruiken.

```
PDF  →  classifier  →  extractor  →  procos_writer  →  <naam>_procos.xltm
                                                            │
                                                            ▼
                  EKB opent in Excel  →  knop "XML Opslaan"  →  XML
                                                            │
                                                            ▼
                                                   ProCos importeert
```

### Kolom-mapping op `klantlijst`-blad (rij 2 en verder)

| Excel-kolom | Header | Onze bron | Regel |
|---|---|---|---|
| A | Aantal | `quantity` | int wanneer heel getal |
| B | Eenheid | hardcoded | altijd `"Stuks"` |
| C | Klantartikel | `device_tag` | leeg als geen tag |
| D | Omschrijving | `description` | newlines blijven |
| E | Fabrikant | `manufacturer` | tekstueel; ProCos koppelt zelf |
| F | Type/bestelnummer | `model_number` (fallback `order_number`) | model_number leidend |
| G | toegeleverd | leeg | (nu altijd) |
| H | ODC code | leeg | (niet beschikbaar in tekening) |
| I | Opmerking | `[<sectielabel>]` + warnings | bv. `[+BASIC]`, `[MC]`, `[SSSA-NC]` |
| J | EAN code | leeg | (niet op tekening) |
| K | x | leeg | template-padding |
| L | Lengte | **template-formule** `=LEN(F)` | aantal karakters van Type/bestelnr |

### Doorvloeiing in template (klantlijst → Daten → XML Ausgabe)

`klantlijst` is wat wij vullen. `Daten` heeft formules die uit `klantlijst`
lezen; `XML Ausgabe` wordt door VBA-macro gevuld bij klik op "XML Opslaan".

**Formules in `Daten` (rij 2):**

| Daten-kol | Header | ← klantlijst | Logica |
|---|---|---|---|
| A | Pos | — | rij-nummer (1, 2, 3, …) |
| B | Menge | A — Aantal | direct |
| C | ME | B — Eenheid | direct |
| D | Kunden Bezeichnung | D — Omschrijving | `LEFT(D, 120)` — afkappen op 120 tekens |
| E | Kunden Artikel | C of E+F | `IF(C<>"", C, E&F)` — fallback Fabrikant+Type |
| F | BstNr | F — Type/bestelnr | direct |
| G | Lieferant | E — Fabrikant | direct |
| H | Typ | — | statisch `"zie bstnr"` |
| I | Hersteller | E — Fabrikant | direct (zelfde als G!) |
| J | BMK | H — ODC code | direct |
| K | Bemerkung | I — Opmerking | direct |
| L | SeitePfad | — | statisch `"not used"` |
| M | Beistellung | G — toegeleverd | `IF(G="", 0, 1)` |
| N | EinbauOrt | — | statisch `"not used"` |
| O | EANNR | J — EAN | direct |

**Belangrijke gevolgen:**
- Onze `manufacturer` gaat 2× door: naar `Lieferant` (leverancier) én `Hersteller` (fabrikant).
- `Daten.D` truncate omschrijving op 120 tekens (template-keuze EKB, niet onze logica).
- `Beistellung` is altijd `0` zolang we `toegeleverd` leeg laten.

---

## Validatieresultaten op alle 5 test-PDFs

| PDF | Pagina's | Runs | Rijen geëxtraheerd | Klantartikel-fill | Sectielabels |
|---|---|---|---|---|---|
| 126-0053 Cabinet Lineator Controller | 37 | 1 (p.33-35) | **76** (100% van ~76) | 76/76 | n.v.t. |
| 9263111 ILCU REV-V1.0 | 13 | 1 (p.10-11) | **51** (100%) | 51/51 | n.v.t. |
| G88000 Network Cabinets | 247 | 4 | **319** (108%) | 319/319 | 4/4 ✅ |
| MAXXeGUARD Beckhoff V4.26 | 37 | 3 | **174** (100%) | 171/174 | 2/3 (TRAY mist) |
| NGB-NGQ V4.0 | 60 | 2 | **348** (102%) | 340/348 | 1/2 (FIELD mist) |

**VBA-macro intact** in alle 5 outputs (`xl/vbaProject.bin` behouden bij
elke round-trip → "XML Opslaan"-knop blijft werken).

---

## Belangrijkste ontwerpkeuzes

### 1. Classifier: 5 signalen, geen AI
Signaal-gebaseerd systeem werkt voorspelbaar zonder trainingsdata. Vijf
onafhankelijke heuristieken per pagina: column headers, vector dichtheid,
row count, title, continuïteit met buren. Drempelwaarden gekalibreerd uit
de 5 test-PDFs maar PDF-breed werkend.

### 2. Twee parsers parallel
PyMuPDF is primair (behoudt spaties), pdfplumber loopt mee voor consensus.
Bij rij-aantal-verschillen >1 komt er een `consensus_warning` in de audit.
Beide parsers krijgen header-row promotion: als de "header" een
tekening-titel blijkt (bv. `EY0X-SSSA`), promoveert pipeline de eerste
datarij naar kolomkop.

### 3. PDF-agnostische kolom-mapping
Geen hardcoded profielen. Headers worden semantisch herkend via
synoniemen-dictionary + fuzzy match (Levenshtein ≤ 2 voor strings ≥ 4
tekens). Onbekende kolommen gaan in `extra_fields` zodat data nooit
verloren gaat.

### 4. Device_tag positional fallback (toegevoegd 2026-04-29)
4 van 5 test-PDFs hebben de device-tag in de eerste kolom **zonder
header**. Probleem: `column_mapper` had niets om op te matchen.
Oplossing in `pipeline.py` (`_apply_device_tag_fallback`):

> Als geen kolom gemapt is op `device_tag`, en kolom 0 heeft geen
> canonical_field, dan wordt kolom 0 als `device_tag` behandeld.

Praktisch alle tekeningen zetten de tag in de eerste kolom — deze
fallback levert 97–100% Klantartikel-fill rate.

Aanvullend: synoniemen-uitbreiding voor multi-line headers zoals
`"Device tag\nPlacement"` → `"device tag placement"`, `"placement"` etc.

### 5. Section-label detectie (run-diff heuristiek)
Voor multi-run PDFs (G88000 met 4 cabinets, MAXXeGUARD met 3 secties,
NGB-NGQ met 2) extraheert `section_detector` korte tokens uit de
pagina-titelblok-zones, en zoekt tokens die **stabiel zijn binnen één
run maar veranderen tussen runs**. Zo herkent hij automatisch `[+BASIC]`,
`[MC]`, `[SSSA-NC]` zonder de EPLAN/Siemens-syntax te kennen.

Werkt perfect voor G88000 (4/4), maar mist sporadisch labels bij
MAXXeGUARD (TRAY) en NGB-NGQ (FIELD). Niet kritiek: ProCos matcht primair
op type+fabrikant, sectielabel in Opmerking is een nice-to-have voor
traceerbaarheid.

### 6. Frontend: Streamlit
Bewust gekozen boven React/Next.js voor snelheid naar werkende POC.
Eén Python-codebase voor UI + backend. Deploybaar op Streamlit Community
Cloud zonder extra infrastructuur.

Voor productie met meerdere gelijktijdige gebruikers: splitsen naar
FastAPI-backend + aparte frontend.

### 7. Twee `src/` packages — naamcollisie opgelost
Zowel `classifier/src/` als `extractor/src/` heten `src` — Python's
imports kunnen daar over struikelen. `frontend/backend/pipeline_service.py`
en `extractor/cli.py` lossen dat op met een **twee-fase `sys.modules`
swap**: eerst classifier laden, dan `src` uit de cache evicten, dan
extractor laden.

---

## Tests draaien

```bash
# Classifier (~2 min — draait tegen alle 5 PDFs in data/)
cd classifier && python -m pytest tests/ -v

# Extractor (~1.5 min)
cd extractor && python -m pytest tests/ -v

# Backend-service smoke-test (~20s — incl. ProCos bytes)
python frontend/backend/test_service.py
```

Verwacht resultaat: **4/4 classifier + 8/8 extractor + 1 backend-test groen**.

Tests verwachten dat de 5 test-PDFs in `data/` staan (gitignored).

---

## CLI-gebruik (voor ontwikkelaars)

```bash
# Auto-classify + extract + ProCos-export (eindproduct)
python extractor/cli.py "data/<pdf>.pdf" --format procos

# Andere formats blijven werken voor dev/debug
python extractor/cli.py "data/<pdf>.pdf" --format xlsx
python extractor/cli.py "data/<pdf>.pdf" --format csv
python extractor/cli.py "data/<pdf>.pdf" --format json

# Handmatige paginaranges (skip classifier)
python extractor/cli.py "data/<pdf>.pdf" --pages "33-35,47-51" --format procos

# Alleen classifier draaien
python classifier/cli.py "data/<pdf>.pdf"
```

Output landt in dezelfde map als de PDF tenzij `--output <dir>` wordt
opgegeven.

---

## Workflow: nieuwe PDF testen

1. Zet de nieuwe PDF in `data/` (lokaal — niet committen).
2. Open Streamlit op http://localhost:8501.
3. Upload de PDF, wacht op verwerking.
4. Klik **"Download de ProCos"** — `.xltm` opent in Excel.
5. Druk op de "XML Opslaan"-knop in `Daten`-blad.
6. Importeer in ProCos.

Als de classifier de stuklijstpagina's niet vindt: gebruik de CLI met
`--pages "x-y"` om te checken of de extractor zelf wel werkt. Zo niet,
voeg ontbrekende synoniemen toe aan `extractor/config.yaml`.

---

## Wijzigingen committen + pushen

```bash
git add .
git commit -m "Korte beschrijving van de wijziging"
git push
```

Eerste commit setup is al gedaan (`gh auth login` uitgevoerd, credentials
in keyring). Streamlit Cloud detecteert de push automatisch en redeployt
in 1–3 min.

`gh` CLI staat **portable** op `%TEMP%\gh_portable\bin\gh.exe` (niet permanent
geïnstalleerd). Als je hem permanent wilt:

```powershell
winget install GitHub.cli
```

---

## Bekende beperkingen

- **Snelheid**: classifier ~10–20s/PDF + extractor ~20–40s voor batch
  van 5–20 stuklijstpagina's. Voor een 247-pagina PDF (G88000) totaal
  ~1 min.
- **Sectie-detectie**: mist sporadisch labels (TRAY, FIELD). Run-diff
  heuristiek kan strikter — verbetering voor later.
- **G88000 device_tag**: bevat een newline (bv. `"-21A1\n&EFS040/21.1:B"`)
  omdat de PDF "Device tag" + "Placement" in één kolom plaatst. ProCos
  kan ermee werken; later splitsen in `device_tag` + `schematic_position`.
- **Single-user**: geen auth, geen job-queue. Upload-limiet 200 MB.
- **Tabel-detectie**: hangt af van PyMuPDF/pdfplumber. Gescande
  (niet-tekstuele) PDFs vereisen OCR — niet geïmplementeerd.
- **OneDrive-sync**: project staat in OneDrive — voor zware ontwikkeling
  kun je `__pycache__/` en `data/` uitsluiten van sync via OneDrive's
  instellingen.

---

## Commit-historie (highlights)

| SHA | Beschrijving |
|---|---|
| `7fb281f` | Initial commit: classifier + extractor + frontend |
| `2d8f444` | Add ProCos export: writer + template + UI integration |
| `9c81c34` | Fix: device_tag (Klantartikel) extracted from headerless first column |
| `35e6231` | Frontend cleanup: single ProCos download button |

Volledige log: `git log --oneline`.

---

## Troubleshooting

**Streamlit Cloud toont oude resultaten na een fix-push:**
1. Hard refresh browser (Ctrl + Shift + R).
2. Klik **"Nieuwe upload"** linksboven — anders zit het oude resultaat
   nog in `session_state`.
3. Re-upload de PDF.

**Lokale Streamlit pikt code-wijzigingen niet op:**
Hot-reload werkt niet altijd voor diep-geïmporteerde modules. Stop het
proces (`Ctrl+C` in de terminal of `taskkill`) en start opnieuw:

```bash
cd frontend && streamlit run app.py
```

**Build-error op Streamlit Cloud:**
Ga naar https://share.streamlit.io → app → **Manage app** → **Logs**.
Daar zie je welke commit-SHA actief is en welke Python-error de build
breekt.

**`gh` CLI weg na herstart:**
Hij staat in `%TEMP%`. Download opnieuw via de PowerShell-stap uit de
git-setup geschiedenis, of installeer permanent met
`winget install GitHub.cli`.

---

## Contact & achtergrond

Initieel gebouwd voor EKB (divisie van Agyle) om elektrische-tekening
stuklijsten automatisch te verwerken via hun ProCos-flow. PDF-agnostische
opzet zodat de tool ook voor toekomstige klanten en toolchains werkt
zonder aanpassingen per leverancier.
