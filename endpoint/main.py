"""OpenAI-compatible API endpoint for the Agyle PDF→ProCos workflow.

Implements two routes used by LibreChat to register and call custom models:

* GET  /v1/models             — list available workflow models
* POST /v1/chat/completions   — execute a workflow (non-streaming for v1)

Both routes require a Bearer token in the Authorization header.

This is Fase 1: a working skeleton that mirrors the OpenAI format exactly.
The actual PDF extraction pipeline is wired in Fase 2 — for now this returns
a deterministic stub response so we can verify the wire-format against
LibreChat / curl before plugging in the real backend.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional, Union

import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Wire in the existing pipeline_service (frontend/backend) so we don't
# duplicate the classifier + extractor logic.
# ---------------------------------------------------------------------------

_ENDPOINT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _ENDPOINT_DIR.parent
_FRONTEND_DIR = _PROJECT_ROOT / "frontend"

if str(_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(_FRONTEND_DIR))

from backend.pipeline_service import (  # noqa: E402
    classify as _classify,
    detect_klant_code as _detect_klant_code,
    extract as _extract,
    extract_from_xlsx as _extract_from_xlsx,
    load_import_referenties_from_path as _load_import_referenties_from_path,
    load_klant_referentielijst_from_path as _load_klant_referentielijst_from_path,
    load_procos_db_from_bytes as _load_procos_db_from_bytes,
    load_procos_db_v2_from_path as _load_procos_db_v2_from_path,
    run_match as _run_match,
    run_match_combined as _run_match_combined,
    to_match_xlsx_bytes as _to_match_xlsx_bytes,
)


# ---------------------------------------------------------------------------
# ProCos artikeldatabase (loaded once at process start, lazily on first use)
# ---------------------------------------------------------------------------

# In production these would come from a nightly ProCos export landing in
# a shared bucket. For the POC we bundle both exports with the Railway
# deploy and load them lazily on first match.
#
# Two databases are kept side-by-side:
#   - v1 (legacy 86k): used as fallback when v2 doesn't have a hit
#   - v2 (new 232k):   primary, supports fab+type / fab+art_code / fab+bestelnr
_PROCOS_V1_PATH = _PROJECT_ROOT / "ProCos-export Artikeldata-excl prijzen.xlsx"
_PROCOS_V2_PATH = _PROJECT_ROOT / "procos_data" / "artikellijst.xlsx"
_KLANT_REF_PATH = _PROJECT_ROOT / "procos_data" / "klant_referentielijsten.xlsx"
_IMPORT_REFS_PATH = _PROJECT_ROOT / "procos_data" / "import_referenties.xlsx"

_PROCOS_DB_V1: Optional[dict] = None
_PROCOS_DB_V1_ERROR: Optional[str] = None
_PROCOS_DB_V2: Optional[dict] = None
_PROCOS_DB_V2_ERROR: Optional[str] = None
_KLANT_DB: Optional[dict] = None
_KLANT_DB_ERROR: Optional[str] = None
_IMPORT_REFS: Optional[dict] = None
_IMPORT_REFS_ERROR: Optional[str] = None


def _get_procos_db_v1() -> tuple[Optional[dict], Optional[str]]:
    """Load (and cache) the legacy 86k ProCos DB."""
    global _PROCOS_DB_V1, _PROCOS_DB_V1_ERROR
    if _PROCOS_DB_V1 is not None or _PROCOS_DB_V1_ERROR is not None:
        return _PROCOS_DB_V1, _PROCOS_DB_V1_ERROR
    if not _PROCOS_V1_PATH.exists():
        _PROCOS_DB_V1_ERROR = (
            f"Legacy ProCos DB niet gevonden (`{_PROCOS_V1_PATH.name}`)."
        )
        return None, _PROCOS_DB_V1_ERROR
    try:
        with open(_PROCOS_V1_PATH, "rb") as fh:
            _PROCOS_DB_V1 = _load_procos_db_from_bytes(fh.read())
        return _PROCOS_DB_V1, None
    except Exception as exc:  # noqa: BLE001
        _PROCOS_DB_V1_ERROR = f"{type(exc).__name__}: {exc}"
        return None, _PROCOS_DB_V1_ERROR


def _get_procos_db_v2() -> tuple[Optional[dict], Optional[str]]:
    """Load (and cache) the new 232k ProCos Artikellijst."""
    global _PROCOS_DB_V2, _PROCOS_DB_V2_ERROR
    if _PROCOS_DB_V2 is not None or _PROCOS_DB_V2_ERROR is not None:
        return _PROCOS_DB_V2, _PROCOS_DB_V2_ERROR
    if not _PROCOS_V2_PATH.exists():
        _PROCOS_DB_V2_ERROR = (
            f"Nieuwe ProCos Artikellijst niet gevonden "
            f"(`procos_data/{_PROCOS_V2_PATH.name}`)."
        )
        return None, _PROCOS_DB_V2_ERROR
    try:
        _PROCOS_DB_V2 = _load_procos_db_v2_from_path(str(_PROCOS_V2_PATH))
        return _PROCOS_DB_V2, None
    except Exception as exc:  # noqa: BLE001
        _PROCOS_DB_V2_ERROR = f"{type(exc).__name__}: {exc}"
        return None, _PROCOS_DB_V2_ERROR


def _get_procos_db() -> tuple[Optional[dict], Optional[str]]:
    """Back-compat shim: returns the v1 DB.

    The new match-flow loads both v1 and v2 directly; this function is
    kept only because `_run_match_response` historically used it for the
    'DB not available' early-out check.
    """
    return _get_procos_db_v1()


def _get_klant_db() -> tuple[Optional[dict], Optional[str]]:
    """Load (and cache) Gino's Klant referentielijsten (45k mappings, 16 klanten)."""
    global _KLANT_DB, _KLANT_DB_ERROR
    if _KLANT_DB is not None or _KLANT_DB_ERROR is not None:
        return _KLANT_DB, _KLANT_DB_ERROR
    if not _KLANT_REF_PATH.exists():
        _KLANT_DB_ERROR = (
            f"Klant referentielijsten niet gevonden "
            f"(`procos_data/{_KLANT_REF_PATH.name}`)."
        )
        return None, _KLANT_DB_ERROR
    try:
        _KLANT_DB = _load_klant_referentielijst_from_path(str(_KLANT_REF_PATH))
        return _KLANT_DB, None
    except Exception as exc:  # noqa: BLE001
        _KLANT_DB_ERROR = f"{type(exc).__name__}: {exc}"
        return None, _KLANT_DB_ERROR


def _get_import_refs() -> tuple[Optional[dict], Optional[str]]:
    """Load (and cache) Gino's Import referenties (HEADER + Eenheden + Adressen)."""
    global _IMPORT_REFS, _IMPORT_REFS_ERROR
    if _IMPORT_REFS is not None or _IMPORT_REFS_ERROR is not None:
        return _IMPORT_REFS, _IMPORT_REFS_ERROR
    if not _IMPORT_REFS_PATH.exists():
        _IMPORT_REFS_ERROR = (
            f"Import referenties niet gevonden "
            f"(`procos_data/{_IMPORT_REFS_PATH.name}`)."
        )
        return None, _IMPORT_REFS_ERROR
    try:
        _IMPORT_REFS = _load_import_referenties_from_path(str(_IMPORT_REFS_PATH))
        return _IMPORT_REFS, None
    except Exception as exc:  # noqa: BLE001
        _IMPORT_REFS_ERROR = f"{type(exc).__name__}: {exc}"
        return None, _IMPORT_REFS_ERROR


# ---------------------------------------------------------------------------
# Extraction cache (sha256(pdf_bytes) -> ExtractionResult)
# This is our "session-store": stateless from LibreChat's view, but the same
# PDF in the same conversation hits the cache on the `match` follow-up so we
# don't re-classify/re-extract a multi-MB drawing twice.
# ---------------------------------------------------------------------------

_EXTRACT_CACHE: dict[str, Any] = {}
_EXTRACT_CACHE_MAX = 50  # bounded — evict oldest on overflow


# ---------------------------------------------------------------------------
# Download store (temp-files: match-rapport.xlsx etc. for chat-side download)
# ---------------------------------------------------------------------------
# Token -> {"filename": str, "mime": str, "data": bytes, "created": float}
# In-memory, single-process. TTL ~30 min via best-effort sweep on each
# new addition (no background task). Sufficient for the POC; productie
# kan dit naar een persistent volume of S3-presigned-url verplaatsen.
_DOWNLOAD_STORE: dict[str, dict[str, Any]] = {}
_DOWNLOAD_STORE_MAX = 100
_DOWNLOAD_TTL_SECONDS = 30 * 60


def _store_download(filename: str, mime: str, data: bytes) -> str:
    """Save bytes under a fresh token, return the token."""
    # Best-effort GC: drop entries older than TTL on every add.
    now = time.time()
    expired = [t for t, v in _DOWNLOAD_STORE.items() if now - v["created"] > _DOWNLOAD_TTL_SECONDS]
    for t in expired:
        _DOWNLOAD_STORE.pop(t, None)
    # Cap size — FIFO eviction.
    while len(_DOWNLOAD_STORE) >= _DOWNLOAD_STORE_MAX:
        _DOWNLOAD_STORE.pop(next(iter(_DOWNLOAD_STORE)))
    token = uuid.uuid4().hex
    _DOWNLOAD_STORE[token] = {
        "filename": filename, "mime": mime, "data": data, "created": now,
    }
    return token


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Read from env so the same code can run with a different key in production.
# Default value is a long opaque string suitable for local dev / curl tests.
API_KEY = os.environ.get(
    "AGYLE_API_KEY",
    "agyle-dev-key-please-override-via-env-AGYLE_API_KEY",
)

# Public origin used to build absolute download URLs that we embed in
# chat replies. LibreChat renders these as <a href> tags; they must be
# absolute (clickable from LibreChat's domain). Default = our production
# Railway URL; override via env when self-hosted by EKB.
PUBLIC_URL = os.environ.get(
    "PUBLIC_URL",
    "https://agyle-api-production.up.railway.app",
)

WORKFLOW_ID = "ekb_procos_matcher"
# Backwards-compat: accept the legacy ID for in-flight conversations that
# were started before the rename. Both resolve to the same workflow.
WORKFLOW_ID_ALIASES = ("agyle_parts_extract",)
WORKFLOW_OWNER = "agyle"
WORKFLOW_CREATED_AT = 1730000000  # static "created" timestamp for the model card


# ---------------------------------------------------------------------------
# OpenAI-compatible Pydantic models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "function"]
    # OpenAI allows content to be either a string or an array of content blocks.
    # We accept both so file/image uploads from LibreChat don't fail validation.
    content: Union[str, list[dict[str, Any]], None] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # We accept any extra fields without failing — OpenAI clients send many.
    model_config = {"extra": "allow"}


class ChatCompletionMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionMessage
    finish_reason: Literal["stop", "length", "content_filter"] = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str


class ModelsListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


# ---------------------------------------------------------------------------
# PDF-detection + extraction helpers
# ---------------------------------------------------------------------------

# MIME types we recognize for input files.
_PDF_MIMES = (
    "application/pdf",
)
_XLSX_MIMES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",  # legacy .xls also tolerated
)


def _detect_upload_kind(data: bytes, filename: str) -> Optional[str]:
    """Identify what kind of upload this is. Returns ``"pdf"``, ``"xlsx"``,
    or ``None``. Uses file-magic first (most reliable) and falls back to
    the filename extension.
    """
    if not data:
        return None
    if data.startswith(b"%PDF-"):
        return "pdf"
    # XLSX files are ZIPs — magic bytes 'PK\x03\x04'
    if data.startswith(b"PK\x03\x04"):
        return "xlsx"
    low = (filename or "").lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".xlsx") or low.endswith(".xls"):
        return "xlsx"
    return None


def _try_upload_from_content(content: Any) -> Optional[tuple[bytes, str, str]]:
    """Inspect one message's ``content`` for an attached PDF or XLSX.

    LibreChat / OpenAI clients can ship file uploads in several shapes:

    - ``{"type": "image_url", "image_url": {"url": "data:<mime>;base64,..."}}``
    - ``{"type": "file", "file": {"name": "...", "data": "<b64>"}}``
    - ``{"type": "file", "file": {"name": "...", "file_data": "<data:.../b64>"}}``
    - plain string starting with ``data:application/...;base64,...`` (rare)

    Returns ``(bytes, filename, kind)`` where kind is ``"pdf"`` or ``"xlsx"``,
    otherwise ``None``.
    """
    # Plain string data-URL
    if isinstance(content, str) and content.startswith("data:"):
        try:
            head, b64 = content.split(",", 1)
        except ValueError:
            return None
        mime = head.split(";")[0][5:]
        data = base64.b64decode(b64)
        kind = _detect_upload_kind(data, f"upload.{mime.rsplit('/', 1)[-1]}")
        if kind:
            ext = "pdf" if kind == "pdf" else "xlsx"
            return data, f"upload.{ext}", kind

    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")

        # Vision-style: image_url with embedded data URL
        if btype == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            if not isinstance(url, str) or not url.startswith("data:"):
                continue
            try:
                _, b64 = url.split(",", 1)
                data = base64.b64decode(b64)
            except Exception:
                continue
            kind = _detect_upload_kind(data, "upload")
            if kind:
                ext = "pdf" if kind == "pdf" else "xlsx"
                return data, f"upload.{ext}", kind

        # file-style: nested data field
        if btype in ("file", "input_file"):
            file_obj = block.get("file") or {}
            name = file_obj.get("name") or file_obj.get("filename") or "upload"
            raw_data = (
                file_obj.get("data")
                or file_obj.get("base64")
                or file_obj.get("file_data")
            )
            if not raw_data:
                continue
            if isinstance(raw_data, str) and raw_data.startswith("data:"):
                try:
                    _, raw_data = raw_data.split(",", 1)
                except ValueError:
                    continue
            try:
                data = base64.b64decode(raw_data)
            except Exception:
                continue
            kind = _detect_upload_kind(data, name)
            if kind:
                return data, name, kind

    return None


def _extract_upload_from_messages(
    messages: list[ChatMessage],
    latest_only: bool = False,
) -> Optional[tuple[bytes, str, str]]:
    """Walk user messages (newest first) looking for an attached PDF or XLSX.

    When ``latest_only=True`` we stop after the most recent user message —
    used to decide whether the *current* turn carries a new upload vs.
    is a follow-up like ``match`` against an earlier upload in history.
    """
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        result = _try_upload_from_content(msg.content)
        if result is not None:
            return result
        if latest_only:
            return None
    return None


def _user_text(msg: ChatMessage) -> str:
    """Return the plain user-typed text from a message, ignoring attachments."""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "")
                if isinstance(txt, str):
                    parts.append(txt)
        return " ".join(parts).strip()
    return ""


def _is_match_command(text: str) -> bool:
    """True if the user typed a `match`-style command (case-insensitive)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Strip trailing punctuation so "match." / "match!" still work
    t = t.rstrip(".!?,;:")
    if t == "match":
        return True
    # Allow phrases that start with "match" + a separator
    return t.startswith("match ") or t.startswith("match\n")


def _md_escape(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _format_extraction_md(result, source_name: str) -> str:
    """Render an ExtractionResult as a tight markdown response."""
    rows = result.rows
    if not rows:
        return (
            f"## Geen stuklijst gevonden in `{source_name}`\n\n"
            "Ik heb geen herkenbare stuklijst-pagina's kunnen extraheren. "
            "Mogelijk is dit een tekening zonder stuklijst-bijlage, of staat de "
            "stuklijst in een gescande (niet-tekstuele) vorm.\n\n"
            "**Probeer opnieuw**: upload een andere PDF."
        )

    pages = sorted({r.source_page for r in rows})
    sections = sorted({r.source_section for r in rows if r.source_section})

    head = [
        f"## Extractie — {source_name}",
        "",
        f"**{len(rows)} rijen** uit pagina('s) {', '.join(str(p) for p in pages)}"
        + (f" · {len(sections)} sectie(s): {', '.join(sections)}" if sections else "")
        + ".",
        "",
    ]

    table = [
        "| # | Klantartikel | Aantal | Omschrijving | Fabrikant | Type/bestelnr |",
        "|---:|---|---:|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        desc = _md_escape(r.description)
        if len(desc) > 70:
            desc = desc[:67] + "..."
        table.append(
            f"| {i} | {_md_escape(r.device_tag)} | "
            f"{_md_escape(r.quantity)} | {desc} | "
            f"{_md_escape(r.manufacturer)} | {_md_escape(r.model_number)} |"
        )

    footer = [
        "",
        "---",
        "",
        "**Volgende stap**",
        "",
        "Typ **`match`** om deze rijen tegen de ProCos artikeldatabase te matchen, "
        "of upload een nieuwe PDF.",
    ]

    return "\n".join(head + table + footer)


def _helper_message() -> str:
    return (
        "## Agyle Parts Extract\n\n"
        "Upload een **PDF-tekening** of **Excel-stuklijst** om te starten. "
        "Ik haal automatisch de rijen eruit en laat per rij zien wat er "
        "gevonden is.\n\n"
        "Typ daarna **`match`** om de geëxtraheerde lijst te matchen tegen "
        "de ProCos artikeldatabase."
    )


def _extract_result_cached(data: bytes, kind: str):
    """Classify + extract (PDF) or read (XLSX), caching by (kind, sha256).

    Returns the ExtractionResult, or ``None`` if no usable rows were
    detected (e.g. PDF without a stuklijst, xlsx without a header row).
    Caches ``None`` too — that's a valid result we don't want to retry.
    """
    cache_key = (kind, hashlib.sha256(data).hexdigest())
    if cache_key in _EXTRACT_CACHE:
        return _EXTRACT_CACHE[cache_key]

    suffix = ".pdf" if kind == "pdf" else ".xlsx"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.write(data)
    tmp.close()
    try:
        if kind == "pdf":
            page_runs = _classify(tmp_path)
            result = _extract(tmp_path, page_runs) if page_runs else None
        elif kind == "xlsx":
            result = _extract_from_xlsx(tmp_path)
            if result is not None and not result.rows:
                result = None
        else:
            result = None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if len(_EXTRACT_CACHE) >= _EXTRACT_CACHE_MAX:
        _EXTRACT_CACHE.pop(next(iter(_EXTRACT_CACHE)))
    _EXTRACT_CACHE[cache_key] = result
    return result


def _run_extraction(data: bytes, source_name: str, kind: str) -> str:
    """Run extraction on the given upload and return a markdown response."""
    result = _extract_result_cached(data, kind)
    if result is None:
        if kind == "pdf":
            return (
                f"## Geen stuklijst-pagina's gedetecteerd in `{source_name}`\n\n"
                "De classifier heeft geen pagina's met een stuklijst herkend. "
                "Mogelijk is dit een tekening zonder stuklijst, of zit de "
                "stuklijst in een aparte PDF.\n\n"
                "**Probeer opnieuw**: upload een andere PDF."
            )
        return (
            f"## Geen herkenbare stuklijst in `{source_name}`\n\n"
            "Ik kon geen tabel met herkenbare kolomnamen vinden in deze Excel "
            "(bijv. `Omschrijving`, `Aantal`, `Type`, `Fabrikant`, ...).\n\n"
            "**Probeer opnieuw**: zorg dat de eerste rij van het stuklijst-blad "
            "kolomnamen bevat."
        )
    return _format_extraction_md(result, source_name)


# ---------------------------------------------------------------------------
# Match (ProCos) helpers
# ---------------------------------------------------------------------------

_STATUS_DISPLAY = {
    "MATCH":                              "MATCH",
    "NIET UNIEK":                         "NIET UNIEK",
    "NIET GEVONDEN":                      "niet gevonden",
    "MATCH (op type alleen)":             "MATCH (op type)",
    "NIET UNIEK (op type alleen)":        "NIET UNIEK (op type)",
    "NIET GEVONDEN (fab niet gemapt)":    "niet gevonden (fab niet gemapt)",
    "GEEN TYPE NR":                       "geen type nr.",
}


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_match_download_link(result, matches, source_name: str) -> str:
    """Generate the match-rapport xlsx, store under a token, return markdown link.

    Returns the empty string when generation fails — caller embeds the
    line conditionally so a writer-error never blocks the match-tabel.
    """
    try:
        data = _to_match_xlsx_bytes(result, matches)
    except Exception:  # noqa: BLE001
        return ""
    stem = source_name.rsplit(".", 1)[0] if "." in source_name else source_name
    filename = f"{stem}_match_rapport.xlsx"
    token = _store_download(filename, _XLSX_MIME, data)
    url = f"{PUBLIC_URL.rstrip('/')}/v1/downloads/{token}"
    return f"📥 [Download volledig match-rapport ({filename})]({url})"


def _format_match_md(result, matches, source_name: str,
                     klant_code: Optional[str] = None,
                     download_link: str = "") -> str:
    """Render extraction + match results as a 9-column markdown table."""
    rows = result.rows
    n_total = len(rows)
    n_match = sum(1 for m in matches if m.status.startswith("MATCH"))
    n_niet_gev = sum(1 for m in matches if m.status.startswith("NIET GEVONDEN"))
    n_niet_uniek = sum(1 for m in matches if m.status.startswith("NIET UNIEK"))
    n_geen = sum(1 for m in matches if m.status == "GEEN TYPE NR")
    pct = (100.0 * n_match / n_total) if n_total else 0.0
    # Combined "any hit" rate: unique MATCH + ambiguous NIET UNIEK. The
    # ambiguous hits aren't 100% certain, but they DO point at a real
    # ProCos article — useful for manual review.
    pct_any = (100.0 * (n_match + n_niet_uniek) / n_total) if n_total else 0.0

    summary = (
        f"**{n_total} rijen** verwerkt — **{n_match} gematched "
        f"({pct:.1f}%)** + {n_niet_uniek} potentiële match (niet uniek) → "
        f"**{n_match + n_niet_uniek} met hit ({pct_any:.1f}%)** · "
        f"{n_niet_gev} niet gevonden"
    )
    if n_geen:
        summary += f" · {n_geen} geen type nr."

    head = [
        f"## Match-resultaat — {source_name}",
        "",
        summary,
        "",
    ]
    if klant_code:
        head.insert(2, f"_Klant gedetecteerd: **{klant_code}** (klant-referentielijst actief)_")
        head.insert(3, "")

    table = [
        "| # | Klantartikel | Aantal | Omschrijving | Fabrikant | "
        "Type/bestelnr | Match | ProCos artikel | ProCos omschrijving |",
        "|---:|---|---:|---|---|---|---|---|---|",
    ]
    for i, (r, m) in enumerate(zip(rows, matches), 1):
        desc = _md_escape(r.description)
        if len(desc) > 50:
            desc = desc[:47] + "..."
        proc_omsch = _md_escape(m.procos_omschrijving)
        if len(proc_omsch) > 50:
            proc_omsch = proc_omsch[:47] + "..."
        status = _STATUS_DISPLAY.get(m.status, m.status)
        table.append(
            f"| {i} | {_md_escape(r.device_tag)} | {_md_escape(r.quantity)} | "
            f"{desc} | {_md_escape(r.manufacturer)} | "
            f"{_md_escape(r.model_number)} | {status} | "
            f"{_md_escape(m.procos_artikel)} | {proc_omsch} |"
        )

    foot = [
        "",
        "---",
        "",
    ]
    if download_link:
        foot.append(download_link)
        foot.append("")
    foot.append("Upload een **nieuw bestand** (PDF of Excel) om opnieuw te starten.")
    return "\n".join(head + table + foot)


def _run_match_response(messages: list[ChatMessage]) -> str:
    """Handle a `match` command: find the prior upload in history, then match."""
    upload = _extract_upload_from_messages(messages, latest_only=False)
    if upload is None:
        return (
            "## Geen upload in deze conversatie\n\n"
            "Ik kan alleen matchen nadat je een **PDF-tekening** of "
            "**Excel-stuklijst** hebt geüpload en geëxtraheerd.\n\n"
            "**Upload eerst een bestand**, en typ daarna `match`."
        )

    data, source_name, kind = upload

    # Load BOTH databases. v2 is primary; v1 is fallback when v2 misses.
    # If at least one is available we can still run the cascade.
    db_v2, err_v2 = _get_procos_db_v2()
    db_v1, err_v1 = _get_procos_db_v1()
    if db_v2 is None and db_v1 is None:
        return (
            "## ProCos artikeldatabase niet beschikbaar\n\n"
            f"- v2 (232k): `{err_v2}`\n"
            f"- v1 (86k):  `{err_v1}`\n\n"
            "Neem contact op met de beheerder."
        )

    # Klant-referentielijst is OPTIONAL — missing is fine, just no step 0.
    klant_db, _ = _get_klant_db()
    # Import referenties is OPTIONAL — missing means we fall back to the
    # legacy 24-entry hardcoded fab_mapping on the v1 cascade path.
    import_refs, _ = _get_import_refs()

    try:
        result = _extract_result_cached(data, kind)
    except Exception as exc:  # noqa: BLE001
        return (
            f"## Fout bij heropvoeren van extractie op `{source_name}`\n\n"
            f"`{type(exc).__name__}: {exc}`"
        )

    if result is None or not result.rows:
        return (
            f"## Geen rijen om te matchen\n\n"
            f"Ik kon geen rijen vinden in `{source_name}`. "
            "Upload eventueel een ander bestand."
        )

    # Klant-detectie: filename + sheet-naam (xlsx-reader stores sheet-name
    # in result.rows[0].source_section).
    sheet_name = result.rows[0].source_section if result.rows else None
    klant_code = _detect_klant_code(source_name, sheet_name) if klant_db else None

    try:
        matches = _run_match_combined(
            result, db_v2, db_v1,
            klant_db=klant_db, klant_code=klant_code,
            import_refs=import_refs,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            f"## Fout bij matchen tegen ProCos\n\n"
            f"`{type(exc).__name__}: {exc}`"
        )

    download_link = _build_match_download_link(result, matches, source_name)
    return _format_match_md(
        result, matches, source_name,
        klant_code=klant_code,
        download_link=download_link,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_bearer(authorization: Optional[str] = Header(default=None)) -> None:
    """Require a valid Bearer token. Raises 401 in OpenAI error format."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Missing Authorization Bearer header",
                    "type": "authentication_error",
                }
            },
        )
    token = authorization.split(" ", 1)[1].strip()
    if token != API_KEY:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid API key",
                    "type": "authentication_error",
                }
            },
        )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Agyle Workflow Endpoint",
    description="OpenAI-compatible API for the PDF→ProCos extraction agent.",
    version="0.1.0-fase1",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Force every error to follow the OpenAI {"error": {...}} envelope."""
    body = exc.detail
    if isinstance(body, dict) and "error" in body:
        return JSONResponse(status_code=exc.status_code, content=body)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": str(body) if body else "Request failed",
                "type": "request_error",
            }
        },
    )


@app.get("/health")
async def health():
    """Unauthenticated liveness probe — useful for monitoring."""
    return {"status": "ok"}


@app.get("/v1/downloads/{token}")
async def download(token: str):
    """Serve a previously-stored download (e.g. match-rapport xlsx).

    Public route — the token itself is the capability (32-char random hex,
    ~128 bits of entropy). TTL 30 min. Clients reach this via the
    download-link the chat-completions handler embeds in match-replies.
    """
    entry = _DOWNLOAD_STORE.get(token)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail={"error": {"message": "Download not found or expired",
                              "type": "not_found"}},
        )
    if time.time() - entry["created"] > _DOWNLOAD_TTL_SECONDS:
        _DOWNLOAD_STORE.pop(token, None)
        raise HTTPException(
            status_code=410,
            detail={"error": {"message": "Download expired",
                              "type": "gone"}},
        )
    safe_name = entry["filename"].replace('"', "")
    return Response(
        content=entry["data"],
        media_type=entry["mime"],
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


def _stream_full_reply(reply_md: str, model: str):
    """Yield an OpenAI-compatible SSE stream for *reply_md*.

    Our workflow is non-streaming under the hood, but LibreChat expects SSE.
    We emit the entire reply in a single content delta — this is the format
    that has been empirically proven to render correctly in LibreChat.
    Variations (multi-chunk content, empty content="" in opening delta)
    have resulted in blank assistant bubbles in some LibreChat versions,
    so we keep this minimal.
    """
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    base = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }

    # 1. opening chunk — role only (no content field)
    open_chunk = {**base, "choices": [{
        "index": 0,
        "delta": {"role": "assistant"},
        "finish_reason": None,
    }]}
    yield f"data: {json.dumps(open_chunk)}\n\n"

    # 2. full content as a single chunk
    content_chunk = {**base, "choices": [{
        "index": 0,
        "delta": {"content": reply_md},
        "finish_reason": None,
    }]}
    yield f"data: {json.dumps(content_chunk)}\n\n"

    # 3. finish chunk
    finish_chunk = {**base, "choices": [{
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }]}
    yield f"data: {json.dumps(finish_chunk)}\n\n"

    # 4. terminator
    yield "data: [DONE]\n\n"


@app.get("/v1/models", response_model=ModelsListResponse,
         dependencies=[Depends(require_bearer)])
async def list_models():
    """OpenAI-compatible model list. Used by LibreChat to populate its dropdown."""
    return ModelsListResponse(
        data=[
            ModelCard(
                id=WORKFLOW_ID,
                created=WORKFLOW_CREATED_AT,
                owned_by=WORKFLOW_OWNER,
            ),
        ]
    )


@app.post("/v1/chat/completions",
          response_model=ChatCompletionResponse,
          dependencies=[Depends(require_bearer)])
async def chat_completions(req: ChatCompletionRequest):
    """Execute a workflow run.

    Fase 1: returns a deterministic stub response that mirrors the OpenAI
    chat.completion envelope. Streaming is not implemented (the spec says
    streaming is not required for the first version).
    """
    if req.model != WORKFLOW_ID and req.model not in WORKFLOW_ID_ALIASES:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": f"Model '{req.model}' not found. "
                               f"Use '{WORKFLOW_ID}'.",
                    "type": "invalid_request_error",
                }
            },
        )

    # ----- Route the user turn -----
    # 1. PDF or XLSX in the *latest* user message → run extraction
    # 2. "match"-style command in latest text     → run match against ProCos
    # 3. Otherwise                                 → welcome / helper message
    upload_latest = _extract_upload_from_messages(req.messages, latest_only=True)
    latest_text = _user_text(req.messages[-1]) if req.messages else ""

    if upload_latest is not None:
        data, source_name, kind = upload_latest
        try:
            reply_md = _run_extraction(data, source_name, kind)
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            reply_md = (
                f"## Fout bij verwerken van `{source_name}`\n\n"
                f"`{type(exc).__name__}: {exc}`\n\n"
                "Upload een ander bestand om opnieuw te proberen."
            )
    elif _is_match_command(latest_text):
        reply_md = _run_match_response(req.messages)
    else:
        reply_md = _helper_message()

    # ----- Stream or single-shot response -----
    if req.stream:
        return StreamingResponse(
            _stream_full_reply(reply_md, req.model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
            },
        )

    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=int(time.time()),
        model=req.model,
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessage(content=reply_md),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(),
    )
