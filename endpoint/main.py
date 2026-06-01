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
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
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
)


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

def _extract_pdf_bytes_from_messages(messages: list[ChatMessage]) -> Optional[tuple[bytes, str]]:
    """Walk the latest user message looking for an attached PDF.

    LibreChat / OpenAI clients can ship file uploads in several shapes:

    - ``{"type": "image_url", "image_url": {"url": "data:application/pdf;base64,..."}}``
    - ``{"type": "file", "file": {"name": "...", "data": "<b64>"}}``
    - ``{"type": "file", "file": {"name": "...", "file_data": "<data:.../b64>"}}``
    - plain string starting with ``data:application/pdf;base64,...`` (rare)

    Returns ``(pdf_bytes, filename)`` if found, otherwise ``None``.
    """
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        content = msg.content
        # Plain string with embedded data-url
        if isinstance(content, str) and content.startswith("data:application/pdf"):
            try:
                b64 = content.split(",", 1)[1]
                return base64.b64decode(b64), "upload.pdf"
            except Exception:
                pass
        # Structured content array
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                # Vision-style: image_url containing a data URL with PDF mime
                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:application/pdf"):
                        try:
                            b64 = url.split(",", 1)[1]
                            return base64.b64decode(b64), "upload.pdf"
                        except Exception:
                            continue
                # file-style: nested data field
                if btype in ("file", "input_file"):
                    file_obj = block.get("file") or {}
                    name = file_obj.get("name") or file_obj.get("filename") or "upload.pdf"
                    data = (
                        file_obj.get("data")
                        or file_obj.get("base64")
                        or file_obj.get("file_data")
                    )
                    if data:
                        if isinstance(data, str) and data.startswith("data:"):
                            data = data.split(",", 1)[1]
                        try:
                            return base64.b64decode(data), name
                        except Exception:
                            continue
        # only inspect the latest user turn
        break
    return None


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
        "Upload een **PDF-tekening** met een stuklijst om te starten. "
        "Ik haal automatisch de stuklijst-pagina's eruit en laat per rij zien "
        "wat er gevonden is.\n\n"
        "Daarna kun je de geëxtraheerde lijst optioneel matchen tegen de "
        "ProCos artikeldatabase (komt in een vervolgstap)."
    )


def _run_extraction(pdf_bytes: bytes, source_name: str) -> str:
    """Persist PDF to a temp file, run classifier + extractor, format markdown."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.write(pdf_bytes)
    tmp.close()
    try:
        page_runs = _classify(tmp_path)
        if not page_runs:
            return (
                f"## Geen stuklijst-pagina's gedetecteerd in `{source_name}`\n\n"
                "De classifier heeft geen pagina's met een stuklijst herkend. "
                "Mogelijk is dit een tekening zonder stuklijst, of zit de "
                "stuklijst in een aparte PDF.\n\n"
                "**Probeer opnieuw**: upload een andere PDF."
            )
        result = _extract(tmp_path, page_runs)
        return _format_extraction_md(result, source_name)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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

    if req.stream:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Streaming is not supported in v0.1. "
                               "Send stream=false (or omit).",
                    "type": "invalid_request_error",
                }
            },
        )

    # ----- Route the user turn -----
    # 1. PDF in the latest user message → run extraction
    # 2. No PDF, no usable text → show the helper / welcome message
    # (Match-mode arrives in Fase 3.)
    pdf_info = _extract_pdf_bytes_from_messages(req.messages)

    if pdf_info is not None:
        pdf_bytes, source_name = pdf_info
        try:
            reply_md = _run_extraction(pdf_bytes, source_name)
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            reply_md = (
                f"## Fout bij verwerken van `{source_name}`\n\n"
                f"`{type(exc).__name__}: {exc}`\n\n"
                "Upload een andere PDF om opnieuw te proberen."
            )
    else:
        reply_md = _helper_message()

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
