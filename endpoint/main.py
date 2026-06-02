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
from fastapi.responses import JSONResponse, StreamingResponse
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
    extract as _extract,
    extract_from_xlsx as _extract_from_xlsx,
    load_procos_db_from_bytes as _load_procos_db_from_bytes,
    run_match as _run_match,
)


# ---------------------------------------------------------------------------
# ProCos artikeldatabase (loaded once at process start, lazily on first use)
# ---------------------------------------------------------------------------

# In production this would come from a nightly ProCos export landing in a
# shared bucket. For the POC we bundle the export with the Railway deploy.
_PROCOS_PATH = _PROJECT_ROOT / "ProCos-export Artikeldata-excl prijzen.xlsx"
_PROCOS_DB: Optional[dict] = None
_PROCOS_DB_ERROR: Optional[str] = None


def _get_procos_db() -> tuple[Optional[dict], Optional[str]]:
    """Return (db, error). Loads on first call; cached for the process lifetime."""
    global _PROCOS_DB, _PROCOS_DB_ERROR
    if _PROCOS_DB is not None or _PROCOS_DB_ERROR is not None:
        return _PROCOS_DB, _PROCOS_DB_ERROR
    if not _PROCOS_PATH.exists():
        _PROCOS_DB_ERROR = (
            f"ProCos artikeldatabase niet gevonden op de server "
            f"(`{_PROCOS_PATH.name}`)."
        )
        return None, _PROCOS_DB_ERROR
    try:
        with open(_PROCOS_PATH, "rb") as fh:
            _PROCOS_DB = _load_procos_db_from_bytes(fh.read())
        return _PROCOS_DB, None
    except Exception as exc:  # noqa: BLE001
        _PROCOS_DB_ERROR = (
            f"Kon ProCos artikeldatabase niet laden: "
            f"{type(exc).__name__}: {exc}"
        )
        return None, _PROCOS_DB_ERROR


# ---------------------------------------------------------------------------
# Extraction cache (sha256(pdf_bytes) -> ExtractionResult)
# This is our "session-store": stateless from LibreChat's view, but the same
# PDF in the same conversation hits the cache on the `match` follow-up so we
# don't re-classify/re-extract a multi-MB drawing twice.
# ---------------------------------------------------------------------------

_EXTRACT_CACHE: dict[str, Any] = {}
_EXTRACT_CACHE_MAX = 50  # bounded — evict oldest on overflow


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Read from env so the same code can run with a different key in production.
# Default value is a long opaque string suitable for local dev / curl tests.
API_KEY = os.environ.get(
    "AGYLE_API_KEY",
    "agyle-dev-key-please-override-via-env-AGYLE_API_KEY",
)

WORKFLOW_ID = "agyle_parts_extract"
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


def _format_match_md(result, matches, source_name: str) -> str:
    """Render extraction + match results as a 9-column markdown table."""
    rows = result.rows
    n_total = len(rows)
    n_match = sum(1 for m in matches if m.status.startswith("MATCH"))
    n_niet_gev = sum(1 for m in matches if m.status.startswith("NIET GEVONDEN"))
    n_niet_uniek = sum(1 for m in matches if m.status.startswith("NIET UNIEK"))
    n_geen = sum(1 for m in matches if m.status == "GEEN TYPE NR")
    pct = (100.0 * n_match / n_total) if n_total else 0.0

    summary = (
        f"**{n_total} rijen** verwerkt — **{n_match} gematched "
        f"({pct:.1f}%)** · {n_niet_gev} niet gevonden · "
        f"{n_niet_uniek} niet uniek"
    )
    if n_geen:
        summary += f" · {n_geen} geen type nr."

    head = [
        f"## Match-resultaat — {source_name}",
        "",
        summary,
        "",
    ]

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
        "Upload een **nieuw bestand** (PDF of Excel) om opnieuw te starten.",
    ]
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

    db, err = _get_procos_db()
    if db is None:
        return (
            f"## ProCos artikeldatabase niet beschikbaar\n\n`{err}`\n\n"
            "Neem contact op met de beheerder."
        )

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

    try:
        matches = _run_match(result, db)
    except Exception as exc:  # noqa: BLE001
        return (
            f"## Fout bij matchen tegen ProCos\n\n"
            f"`{type(exc).__name__}: {exc}`"
        )

    return _format_match_md(result, matches, source_name)


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
    if req.model != WORKFLOW_ID:
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
