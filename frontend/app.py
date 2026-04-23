"""Item-list Extractor — Streamlit entry point.

Orchestrates the three screens: upload -> processing -> results, wires
the UI components to the pipeline_service backend, and applies the
EKB-branded theme overlay.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from backend.pipeline_service import (
    classify,
    extract,
    rows_to_dataframe,
    to_csv_bytes,
    to_json_bytes,
    to_xlsx_bytes,
)
from components.processing import render_processing
from components.results import render_results
from components.upload import render_upload


_HERE = Path(__file__).resolve().parent
_CSS_PATH = _HERE / "assets" / "custom.css"


def _inject_css() -> None:
    """Inject theme polish once per session."""
    if _CSS_PATH.exists():
        css = _CSS_PATH.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _init_state() -> None:
    st.session_state.setdefault("stage", "upload")
    st.session_state.setdefault("files", None)
    st.session_state.setdefault("processed", None)


def _reset() -> None:
    st.session_state.stage = "upload"
    st.session_state.files = None
    st.session_state.processed = None


def main() -> None:
    st.set_page_config(
        page_title="EKB · Item-list Extractor",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    _init_state()

    stage = st.session_state.stage

    if stage == "upload":
        files = render_upload()
        if files:
            st.session_state.files = files
            st.session_state.stage = "processing"
            st.rerun()

    elif stage == "processing":
        processed = render_processing(
            st.session_state.files,
            classify_fn=classify,
            extract_fn=extract,
        )
        st.session_state.processed = processed
        st.session_state.stage = "results"
        st.write("")
        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Verder →", type="primary", use_container_width=True):
                st.rerun()
        with c2:
            if st.button("Opnieuw uploaden", use_container_width=False):
                _reset()
                st.rerun()

    elif stage == "results":
        top_l, top_r = st.columns([5, 1])
        with top_r:
            if st.button("Nieuwe upload", use_container_width=True):
                _reset()
                st.rerun()

        render_results(
            st.session_state.processed or [],
            rows_to_df_fn=rows_to_dataframe,
            xlsx_bytes_fn=to_xlsx_bytes,
            csv_bytes_fn=to_csv_bytes,
            json_bytes_fn=to_json_bytes,
        )


if __name__ == "__main__":
    main()
