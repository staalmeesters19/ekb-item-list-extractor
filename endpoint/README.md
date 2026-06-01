# Agyle Workflow Endpoint

OpenAI-compatible HTTP-API voor de PDF→ProCos extractie-workflow. Bedoeld om
gekoppeld te worden aan EKB's LibreChat als custom endpoint.

## Routes

| Method | Path | Auth | Doel |
|---|---|---|---|
| GET | `/health` | nee | liveness check |
| GET | `/v1/models` | Bearer | lijst van workflow-IDs voor LibreChat-dropdown |
| POST | `/v1/chat/completions` | Bearer | workflow uitvoeren (non-streaming) |

## Snel starten

```bash
pip install -r requirements.txt

# Standaard dev-key — pas aan voor productie
export AGYLE_API_KEY=agyle-dev-key-please-override-via-env-AGYLE_API_KEY   # Linux / WSL
$env:AGYLE_API_KEY = "agyle-dev-key-please-override-via-env-AGYLE_API_KEY" # Windows PowerShell

uvicorn main:app --host 0.0.0.0 --port 8000
```

Draait op `http://localhost:8000`. Streamlit kan tegelijk op poort 8501 blijven
draaien — ze bijten elkaar niet.

## Test met curl

```bash
KEY="agyle-dev-key-please-override-via-env-AGYLE_API_KEY"

# Health
curl http://localhost:8000/health

# Models
curl http://localhost:8000/v1/models -H "Authorization: Bearer $KEY"

# Chat completion (Fase 1: stub-response)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agyle_parts_extract",
    "messages": [{"role": "user", "content": "test"}]
  }'

# Auth-fail (no header)
curl -i http://localhost:8000/v1/models
```

## Fase-status

**Fase 1 (klaar)** — Werkend skelet:
- `/v1/models` + `/v1/chat/completions` retourneren OpenAI-correcte envelopes
- Bearer-auth + correct error-format (`{"error": {...}}`)
- Accepteert zowel string-content als array-content (LibreChat file-uploads)
- Niet-streaming response — zoals afgesproken in de spec

**Fase 2 (volgende stap)** — pipeline aansluiten:
- Wrapper functie die `extract()` + `run_match()` uit `frontend/backend/pipeline_service.py` aanroept
- PDF-input via base64 in messages-content
- Output als markdown-tabel + optionele Excel-attachment

## Productie-notes

- Zet `AGYLE_API_KEY` via env, nooit committen
- LibreChat-config voegt deze toe als custom endpoint: `baseURL=http://<host>:8000/v1`, `apiKey=...`, `models.fetch=true`
- Voor publieke exposure: zet er reverse-proxy (Nginx/Traefik) + HTTPS voor
