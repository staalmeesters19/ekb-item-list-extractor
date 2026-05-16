"""Item-list Extractor — Streamlit entry point.

Orchestrates the screens (upload → processing → results → matching → match_results),
wires the UI components to the pipeline_service backend, and applies the
EKB-branded theme overlay.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from backend.pipeline_service import (
    classify,
    extract,
    rows_to_dataframe,
    run_match,
    to_match_xlsx_bytes,
    to_niet_gevonden_xlsx_bytes,
    to_procos_bytes,
    to_procos_xml_bytes,
    to_xlsx_bytes,
)
from components.header import render_header
from components.match_results import render_match_results
from components.matching import render_matching
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
    # ProCos-database + match state survives reset (within the session)
    st.session_state.setdefault("procos_db", None)
    st.session_state.setdefault("procos_db_n", None)
    st.session_state.setdefault("procos_db_name", None)
    st.session_state.setdefault("match_results_by_name", None)


def _reset() -> None:
    st.session_state.stage = "upload"
    st.session_state.files = None
    st.session_state.processed = None
    # Clear match results — they're tied to the previous processed set
    st.session_state.match_results_by_name = None
    # KEEP procos_db loaded; user uploaded once, no need to repeat


def main() -> None:
    st.set_page_config(
        page_title="EKB · Item-list Extractor",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    _init_state()
    render_header()

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
            raw_xlsx_bytes_fn=to_xlsx_bytes,
        )

    elif stage == "matching":
        top_l, top_r = st.columns([5, 1])
        with top_r:
            if st.button("Nieuwe upload", use_container_width=True):
                _reset()
                st.rerun()

        render_matching(
            st.session_state.processed or [],
            run_match_fn=run_match,
        )

    elif stage == "match_results":
        render_match_results(
            st.session_state.processed or [],
            rows_to_df_fn=rows_to_dataframe,
            procos_bytes_fn=to_procos_bytes,
            procos_xml_bytes_fn=to_procos_xml_bytes,
            match_xlsx_bytes_fn=to_match_xlsx_bytes,
            niet_gevonden_xlsx_bytes_fn=to_niet_gevonden_xlsx_bytes,
        )


if __name__ == "__main__":
    main()
