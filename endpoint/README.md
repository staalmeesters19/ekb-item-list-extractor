# Agyle Parts Extract — OpenAI-compatible Workflow Endpoint

FastAPI service die zich voordoet als een OpenAI chat-completions API. In een
chat-gesprek kun je:

1. Een **PDF-tekening** óf een **klant-Excel-stuklijst** uploaden →
   gestructureerde rijen + markdown-tabel terug
2. `match` typen → 9-kolom match-tabel tegen de ProCos artikeldatabase

Bedoeld om als custom endpoint gekoppeld te worden aan LibreChat (of elke
andere OpenAI-compatible chat-frontend).

---

## Endpoints

| Method | Path | Auth | Doel |
|---|---|---|---|
| GET  | `/health` | nee | liveness check |
| GET  | `/v1/models` | Bearer | lijst van workflow-IDs voor LibreChat-dropdown |
| POST | `/v1/chat/completions` | Bearer | workflow uitvoeren (SSE-stream of JSON) |

Model-ID: `agyle_parts_extract`.

---

## Match-pipeline (alles geactiveerd)

Per rij draait deze cascade — eerste unieke hit wint:

```
0.  klant-ref       (klantcode, klant-artikel)  → EKB-artikel   [als klant herkend]
3.  v2: fab + type           (Fabrikaat-tekst direct)
4.  v2: fab + art_code
5.  v2: fab + bestelnr_lev
6.  v2: type-only
3v1. v1: fab + type          (legacy 86k, Adressen 738-entry + wildcards)
6v1. v1: type-only
```

Match-rates over onze 8 test-inputs (1516 rijen):

| | rate | Δ vs baseline |
|---|---:|---:|
| Baseline (hardcoded 24-entry mapping) | 73.7% | – |
| Fase A — v2 cascade + v1 fallback | 78.1% | +4.4pp |
| Fase B — + klant-referentielijst (45k mappings) | 81.9% | +8.2pp |
| Fase C — + Adressen 738-entry + wildcards | **83.2%** | **+9.5pp** |

---

## Hosting & deploy

### Productie (Railway)

| Service | URL |
|---|---|
| API | `https://agyle-api-production.up.railway.app/v1` |
| LibreChat (test) | gehost op Railway, gekoppeld via `librechat.yaml` |

### Lokaal draaien

```bash
pip install -r requirements.txt

# Pas deze key aan voor productie
$env:AGYLE_API_KEY = "agyle-dev-key-please-override-via-env-AGYLE_API_KEY"

cd endpoint
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Vereiste data-bestanden

De service laadt deze lazy bij de eerste match-call. **Niet in git**
(gitignored — bevatten confidential klant-data), maar **wel mee-ge-upload
door `railway up`** via de negate-rules in `.railwayignore`.

| Bestand | Locatie | Inhoud | Bron |
|---|---|---|---|
| Legacy ProCos export | `/ProCos-export Artikeldata-excl prijzen.xlsx` | 86k artikelen, oude format | EKB pre-Q2 2026 |
| Nieuwe Artikellijst | `/procos_data/artikellijst.xlsx` | 232k artikelen, multi-route lookup | Gino, 2026-05-28 |
| Klant referentielijsten | `/procos_data/klant_referentielijsten.xlsx` | 45k mappings, 16 klanten | Gino, 2026-05-28 |
| Import referenties | `/procos_data/import_referenties.xlsx` | 738 fab-mappings, eenheden, char-strip | Gino, 2026-05-28 |

In productie bij EKB zouden deze 4 bestanden komen uit een nightly ProCos-export
naar een gedeelde bucket (zie "Productie-architectuur" hieronder).

---

## Aansluiten op een andere LibreChat-instance

Voeg dit toe aan `librechat.yaml` van die instance:

```yaml
version: 1.2.5

endpoints:
  custom:
    - name: 'Agyle Parts Extract'
      apiKey: '${AGYLE_API_KEY}'     # zet in je .env
      baseURL: 'https://agyle-api-production.up.railway.app/v1'
      models:
        default:
          - 'agyle_parts_extract'
        fetch: true
      titleConvo: false
      modelDisplayLabel: 'Agyle Parts Extract'

# Voor xlsx-uploads via 'Upload to Provider' (anders alleen PDF):
fileConfig:
  endpoints:
    "Agyle Parts Extract":
      fileLimit: 5
      fileSizeLimit: 25
      totalSizeLimit: 50
      supportedMimeTypes:
        - "application/pdf"
        - "application/vnd\\.openxmlformats-officedocument\\..*"
        - "application/vnd\\.ms-excel.*"
        - "application/octet-stream"
```

Restart de LibreChat-container daarna. De gebruiker krijgt het model
`agyle_parts_extract` in zijn dropdown.

---

## Productie-architectuur (voor EKB)

Drie deploy-modellen:

| Model | Wie host | Data-flow |
|---|---|---|
| **A. Onze Railway** (huidig) | Agyle | Klant-data passeert Agyle-cloud — minder geschikt voor productie |
| **B. EKB self-host** (aanbevolen) | EKB IT | Docker-image van deze repo draait naast LibreChat in EKB's eigen Docker-compose. Data blijft binnen EKB-netwerk |
| **C. Hybrid** | Agyle hostet endpoint, EKB pushed nightly data | Tier 2A: EKB-cron schrijft naar `/admin/procos-upload` (nog te bouwen) |

Bij **Model B** kun je `docker-compose.yml` van EKB's LibreChat uitbreiden met:

```yaml
services:
  agyle-api:
    image: ghcr.io/agyle/parts-extract:latest    # publish-target nog te bouwen
    environment:
      - AGYLE_API_KEY=${AGYLE_API_KEY}
    volumes:
      - ./procos-data:/app/procos_data           # EKB plaatst hier de 3 Gino-bestanden
    ports:
      - "8080:8080"
```

En in `librechat.yaml` van EKB: `baseURL: http://agyle-api:8080/v1` (intern
docker-netwerk).

---

## Code-architectuur

```
endpoint/
  main.py             FastAPI, OpenAI-compatible routing, lazy DB-loaders
extractor/
  src/
    xlsx_reader.py    klant-Excel → ExtractionResult (zelfde shape als PDF)
    pipeline.py       PDF → ExtractionResult (classify + extract + post-process)
    matcher.py        v1 + v2 + klant-ref + Adressen cascade
    column_mapper.py  synoniem-gedreven kolom→canonical mapping
    post_processor.py quantity-parse, pipe-pattern split, unicode cleanup
frontend/
  backend/
    pipeline_service.py  shim die endpoint en streamlit-frontend delen
```

---

## Test met curl

```bash
KEY="agyle-dev-key-please-override-via-env-AGYLE_API_KEY"
URL="https://agyle-api-production.up.railway.app"

# Health
curl $URL/health

# Models
curl $URL/v1/models -H "Authorization: Bearer $KEY"

# Welcome message (geen file uploaded)
curl -X POST $URL/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"agyle_parts_extract","messages":[{"role":"user","content":"hallo"}],"stream":false}'
```

Voor PDF/XLSX uploads: stuur base64-data in een `content[]`-array
(`{"type":"file","file":{"name":"x.pdf","data":"<b64>"}}`). De endpoint
herkent het format via magic-bytes en routeert naar de juiste extractor.
