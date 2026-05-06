"""Page header with EKB logo and subtle app-name caption.

Rendered above every screen so branding stays consistent across stages.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st


_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "ekb-logo.svg"


def _logo_data_uri() -> str:
    if not _LOGO_PATH.exists():
        return ""
    encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def render_header() -> None:
    """Render the EKB-branded header strip at the top of the page."""
    uri = _logo_data_uri()
    if not uri:
        return
    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:1rem;
            padding:0.25rem 0 1.25rem 0;
            border-bottom:1px solid #E2E8F0;
            margin-bottom:1.5rem;
        ">
            <img src="{uri}" alt="EKB" style="height:40px; width:auto;" />
            <div style="
                font-size:0.8125rem;
                color:#64748B;
                font-weight:500;
                letter-spacing:0.02em;
                text-transform:uppercase;
            ">
                Item-list Extractor
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
