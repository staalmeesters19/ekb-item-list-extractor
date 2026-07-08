# EKB ProCos Matcher — deploy-instructie voor EKB IT

Deze service is een OpenAI-compatible chat-endpoint dat via LibreChat
aangesproken wordt. Onder de motorkap draait Python 3.13 + FastAPI.
De hele service draait in één Docker-container.

**Contactpersoon Agyle:** Joris Merkx · joris.merkx@agyle.nl

---

## 1. Wat je nodig hebt

| Item | Wat |
|---|---|
| Docker + Docker Compose | v20.10+ / v2+ |
| ~3 GB RAM | De service houdt 232k artikelen in geheugen |
| ~2 CPU cores | Voor de matching + extractie |
| Poort 8080 (of alternatief) | Waar de service luistert |
| Persistente storage locatie | Voor feedback + ProCos-data volumes |
| Netwerk-toegang tot ProCos-MCP | Alleen relevant na fase 2 — MCP-koppeling |

**Compatibele OS:** Linux (aanbevolen), Windows Server met Docker Desktop, of Docker in WSL2.

---

## 2. Bestanden die je nodig hebt

Uit deze repo (`ekb-item-list-extractor`):

```
Dockerfile
docker-compose.yml
requirements.txt
extractor/       (code)
classifier/      (code)
frontend/        (code — alleen backend/pipeline_service.py wordt gebruikt)
endpoint/        (code)
```

**En één van de twee:** ProCos-referentiedata (zie sectie 3).

---

## 3. ProCos-referentiedata

De matching heeft vier data-bestanden nodig. Deze plaats je vóór de
eerste start in een directory op de host — de container mount 'm read-only.

**Bestanden (van Gino, meest actueel):**

```
procos-export/
├── artikellijst.xlsx               (232k artikelen — verplicht)
├── klant_referentielijsten.xlsx    (45k mappings — verplicht)
├── import_referenties.xlsx         (fab-mapping + eenheden — verplicht)
└── ProCos-export-legacy.xlsx       (86k legacy — optioneel, wordt gebruikt als fallback)
```

**Plaatsing op host** (voorbeeld):

```
/mnt/procos-export/
├── artikellijst.xlsx
├── klant_referentielijsten.xlsx
├── import_referenties.xlsx
└── ProCos-export-legacy.xlsx
```

Deze directory verwijs je aan via de env-var `PROCOS_DATA_HOST` in `.env` (zie sectie 5).

> **Later** (na integratie met ProCos-MCP): deze files hoeven dan niet meer op disk — de
> service haalt data live op via de MCP. Voor de eerste deployment werken we met files.

---

## 4. Eenmalige setup

```bash
# 1. Clone de repo op de EKB-server
git clone https://github.com/staalmeesters19/ekb-item-list-extractor.git
cd ekb-item-list-extractor

# 2. Maak een .env aan (nooit committen — zit in .gitignore)
cp .env.example .env  # of maak zelf een .env, zie sectie 5
nano .env

# 3. Maak feedback-directory aan (persistent, container schrijft hier)
mkdir -p /var/lib/agyle-api/feedback

# 4. Zorg dat ProCos-data-directory bestaat (zie sectie 3)
ls /mnt/procos-export/  # moet 4 xlsx-bestanden tonen

# 5. Build + start
docker compose build
docker compose up -d

# 6. Verifieer
curl http://localhost:8080/health
# Verwacht: {"status":"ok"}
```

---

## 5. `.env` inhoud

Kopieer onderstaande naar `.env` (naast de `docker-compose.yml`) en vul in:

```bash
# ── Auth ─────────────────────────────────────────────
# Lange random-string. Wordt door LibreChat als Bearer-token gestuurd.
# Genereer met bv: python -c "import secrets; print(secrets.token_urlsafe(48))"
AGYLE_API_KEY=zet-hier-een-lange-random-string

# ── URL waarop de service publiek is ─────────────────
# Wordt door de chat-response gebruikt om absolute download-links te bouwen.
# In EKB-context: interne DNS-naam of IP + poort.
PUBLIC_URL=http://ekb-agyle-api.internal:8080

# ── Volume-locaties op de host ───────────────────────
# ProCos-data — read-only mounted in de container
PROCOS_DATA_HOST=/mnt/procos-export

# Feedback JSONL — persistente storage, container schrijft hier
FEEDBACK_HOST=/var/lib/agyle-api/feedback
```

---

## 6. LibreChat-koppeling

Voeg deze snippet toe aan EKB's `librechat.yaml`:

```yaml
endpoints:
  custom:
    - name: 'EKB ProCos Matcher'
      apiKey: '${EKB_PROCOS_KEY}'          # dezelfde key als AGYLE_API_KEY hierboven
      baseURL: 'http://agyle-api:8080/v1'  # interne Docker-netwerk-URL
      models:
        default:
          - 'ekb_procos_matcher'
        fetch: true
      titleConvo: false
      modelDisplayLabel: 'EKB ProCos Matcher'

fileConfig:
  endpoints:
    "EKB ProCos Matcher":
      fileLimit: 5
      fileSizeLimit: 25
      totalSizeLimit: 50
      supportedMimeTypes:
        - "application/pdf"
        - "application/vnd\\.openxmlformats-officedocument\\..*"
        - "application/vnd\\.ms-excel.*"
        - "application/octet-stream"
```

En in LibreChat's `.env`:

```bash
EKB_PROCOS_KEY=<dezelfde-random-string-als-AGYLE_API_KEY>
```

**Nb.**: als `agyle-api` en LibreChat in aparte Docker-compose-projects draaien,
moet er een gedeeld extern Docker-netwerk zijn zodat `http://agyle-api:8080/v1`
kan resolven. Alternatief: exposen op host-IP en `baseURL` daarnaar wijzen.

---

## 7. Updates deployen

Wanneer Agyle een nieuwe versie levert:

```bash
cd /path/to/ekb-item-list-extractor
git pull origin main
docker compose build
docker compose up -d       # container-restart, ~30 seconden downtime
```

Rollback naar vorige versie:

```bash
git log --oneline -5       # vind commit-hash van vorige goede versie
git checkout <hash>
docker compose build
docker compose up -d
```

---

## 8. Monitoring & debugging

### Health check

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

### Logs

```bash
docker compose logs -f agyle-api       # live tail
docker compose logs --tail=200 agyle-api
```

### Feedback ophalen (van gebruikers)

```bash
curl -H "Authorization: Bearer ${AGYLE_API_KEY}" \
     http://localhost:8080/admin/feedback
```

Retourneert JSON met alle feedback-entries.

### Container binnenkijken (troubleshoot)

```bash
docker compose exec agyle-api /bin/bash
```

---

## 9. Netwerk / security overwegingen

| Aspect | Aanbeveling |
|---|---|
| Public exposure | Alleen intern netwerk. Externe toegang alleen via reverse-proxy (nginx/traefik) met TLS |
| API-key rotatie | Regenereer bij vermoedens van compromis: nieuwe `AGYLE_API_KEY` in `.env` + LibreChat `.env` + `docker compose up -d` |
| Klantdata | Klant-PDFs / Excels staan alleen in geheugen tijdens processing. Niks wordt persistent bewaard behalve feedback + downloads-cache (30 min TTL) |
| ProCos-data | Read-only mounted, container kan hem niet wijzigen |

---

## 10. Bekende beperkingen

- **Cold-start match**: eerste `match`-call na service-start laadt de ProCos-DBs (~30–45 s).
  Daarna zit alles in geheugen — vervolgcalls binnen milliseconden.
- **Memory footprint**: ~1.5 GB steady-state door in-memory DBs. Docker resource-limit staat op 3 GB.
- **File-upload limiet**: 25 MB per bestand, 50 MB totaal per chat (LibreChat-side geconfigureerd).

---

## 11. Vragen / support

Alle vragen naar **Joris Merkx** — joris.merkx@agyle.nl.

Voor snelle diagnose stuur mee:
- Output van `docker compose logs --tail=100 agyle-api`
- Output van `curl http://localhost:8080/health`
- Body van de `.env` (zonder secrets!)
