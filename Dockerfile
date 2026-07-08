# syntax=docker/dockerfile:1.6
# ============================================================================
# EKB ProCos Matcher — production Docker image
#
# Build:
#   docker build -t ekb-procos-matcher:latest .
#
# Run (development, bundled data):
#   docker run -p 8080:8080 \
#     -e AGYLE_API_KEY=your-secret-key \
#     ekb-procos-matcher:latest
#
# Run (production, mounted data + persistent feedback):
#   docker run -p 8080:8080 \
#     -e AGYLE_API_KEY=your-secret-key \
#     -e PROCOS_DATA_DIR=/data/procos \
#     -e FEEDBACK_DIR=/data/feedback \
#     -v /path/to/procos-data:/data/procos:ro \
#     -v /path/to/feedback:/data/feedback \
#     ekb-procos-matcher:latest
# ============================================================================

FROM python:3.13-slim AS runtime

# ---- OS deps -----------------------------------------------------------------
# poppler-utils = niet nodig, PyMuPDF brengt eigen renderer mee
# libgomp1      = OpenMP runtime; sommige numeric libs vragen 't
# curl          = handig voor container-side health checks bij debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Non-root user -----------------------------------------------------------
# Draaien als root binnen container is een security-anti-pattern.
RUN groupadd -r app && useradd -r -g app -d /app -s /usr/sbin/nologin app

# ---- Working directory + Python setup ---------------------------------------
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---- Python deps (separate layer voor caching) -------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- Application code --------------------------------------------------------
# Kopiëren in specifieke volgorde: eerst wat zelden verandert, dan wat vaak
# verandert — snelste rebuilds tijdens development.
COPY extractor/  ./extractor/
COPY classifier/ ./classifier/
COPY frontend/   ./frontend/
COPY endpoint/   ./endpoint/

# ProCos-referentiedata: BUNDLED in de image voor de eerste EKB-deployment.
# In productie zal EKB waarschijnlijk hun eigen data-directory mounten via
# een volume; dan wordt PROCOS_DATA_DIR gezet en negeert de app de bundled
# bestanden. Deze COPY is er dus voor dev/demo, niet voor productie-doel.
COPY procos_data/                                        ./procos_data/
COPY "ProCos-export Artikeldata-excl prijzen.xlsx"       ./

# ---- Runtime dirs (feedback JSONL, download-cache) ---------------------------
RUN mkdir -p /app/feedback /app/downloads \
    && chown -R app:app /app

# ---- Env vars — sane defaults, overridable at run-time ----------------------
ENV PORT=8080 \
    PUBLIC_URL="http://localhost:8080" \
    PROCOS_DATA_DIR="/app/procos_data" \
    PROCOS_V1_PATH="/app/ProCos-export Artikeldata-excl prijzen.xlsx" \
    FEEDBACK_DIR="/app/feedback"

# ---- Health check ------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# ---- Switch to non-root ------------------------------------------------------
USER app

EXPOSE 8080

# uvicorn direct — geen gunicorn-tussenlaag; onze workload is een enkelvoudige
# ASGI-app met langlopende in-memory DB's, dus meerdere workers zouden
# geheugengebruik vervierdubbelen zonder throughput-winst.
CMD ["python", "-m", "uvicorn", "endpoint.main:app", \
     "--host", "0.0.0.0", "--port", "8080"]
