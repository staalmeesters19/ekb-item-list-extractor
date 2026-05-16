"""Match-results screen — dashboard + filterable table + match exports.

Reached from the matching transition screen. Shows aggregate stats,
a table with match-status columns, and download buttons for the match
artefacts (match-rapport + niet-gevonden lijst + ProCos exports).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PROCOS_MIME = "application/vnd.ms-excel.template.macroEnabled.12"
PROCOS_XML_MIME = "text/xml"


def _agg_stats(match_results_by_name):
    total = 0
    by_status = {}
    for v in match_results_by_name.values():
        for m in v:
            total += 1
            by_status[m.status] = by_status.get(m.status, 0) + 1
    matched = sum(c for s, c in by_status.items() if s.startswith("MATCH"))
    niet_gev = sum(c for s, c in by_status.items() if s.startswith("NIET GEVONDEN"))
    niet_uniek = sum(c for s, c in by_status.items() if s.startswith("NIET UNIEK"))
    geen_type = by_status.get("GEEN TYPE NR", 0)
    return {
        "total": total,
        "match": matched,
        "niet_gevonden": niet_gev,
        "niet_uniek": niet_uniek,
        "geen_type": geen_type,
        "match_pct": (100 * matched / total) if total else 0.0,
        "by_status": by_status,
    }


def _build_combined_df(processed_items, rows_to_df_fn, match_results_by_name):
    frames = []
    for item in processed_items:
        df = rows_to_df_fn(item["result"])
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, "pdf", item["name"])
        matches = match_results_by_name.get(item["name"])
        if matches and len(matches) == len(df):
            df["match_status"] = [m.status for m in matches]
            df["procos_artikel"] = [m.procos_artikel for m in matches]
            df["procos_omschrijving"] = [m.procos_omschrijving for m in matches]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _column_config(df):
    cfg = {}
    for col in df.columns:
        if col == "quantity":
            try:
                cfg[col] = st.column_config.NumberColumn("quantity", format="%d")
            except Exception:  # noqa: BLE001
                cfg[col] = st.column_config.TextColumn("quantity", width="medium")
        elif col == "description":
            cfg[col] = st.column_config.TextColumn("description", width="large")
        elif col == "match_status":
            cfg[col] = st.column_config.TextColumn("match", width="medium")
        elif col == "procos_artikel":
            cfg[col] = st.column_config.TextColumn("ProCos artikel", width="medium")
        elif col == "procos_omschrijving":
            cfg[col] = st.column_config.TextColumn("ProCos omschrijving", width="large")
        else:
            cfg[col] = st.column_config.TextColumn(col, width="medium")
    return cfg


def render_match_results(
    processed_items,
    rows_to_df_fn,
    procos_bytes_fn,
    procos_xml_bytes_fn,
    match_xlsx_bytes_fn,
    niet_gevonden_xlsx_bytes_fn,
):
    """Render the match-results dashboard + table + exports."""
    match_results_by_name = st.session_state.get("match_results_by_name") or {}
    if not processed_items or not match_results_by_name:
        st.info("Geen match-resultaten beschikbaar.")
        if st.button("← Terug naar resultaten"):
            st.session_state.stage = "results"
            st.rerun()
        return

    # --- Navigation buttons (top) ---
    nav1, nav2, nav3 = st.columns([1, 1, 4])
    with nav1:
        if st.button("← Resultaten", use_container_width=True):
            st.session_state.stage = "results"
            st.rerun()
    with nav2:
        if st.button("Nieuwe upload", use_container_width=True):
            st.session_state.stage = "upload"
            st.session_state.files = None
            st.session_state.processed = None
            st.session_state.match_results_by_name = None
            st.rerun()

    st.write("")
    st.markdown("### Match-resultaten")
    st.write("")

    # --- Dashboard: 4 metric cards ---
    stats = _agg_stats(match_results_by_name)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Match %", f"{stats['match_pct']:.1f}%")
    m2.metric("MATCH", stats["match"])
    m3.metric("NIET GEVONDEN", stats["niet_gevonden"])
    m4.metric("NIET UNIEK", stats["niet_uniek"])

    # Second row: total + geen type nr (optional but informative)
    if stats["geen_type"] > 0:
        st.write("")
        m5, m6 = st.columns([1, 5])
        m5.metric("Geen type nr.", stats["geen_type"])

    st.write("")

    # --- Filters ---
    combined = _build_combined_df(processed_items, rows_to_df_fn, match_results_by_name)
    pdf_names = [item["name"] for item in processed_items]
    multi = len(processed_items) > 1
    sections = sorted({
        r.source_section for item in processed_items
        for r in item["result"].rows if r.source_section
    })

    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    with f1:
        if multi:
            pdf_choice = st.selectbox("PDF", ["Alle"] + pdf_names, index=0, key="mr_pdf")
        else:
            pdf_choice = "Alle"
            st.text_input("PDF", value=pdf_names[0], disabled=True, key="mr_pdf_single")
    with f2:
        section_choice = st.multiselect("Secties", sections, key="mr_sections")
    with f3:
        status_options = sorted(combined["match_status"].dropna().unique().tolist()) if "match_status" in combined.columns else []
        status_choice = st.multiselect("Match-status", status_options, key="mr_status")
    with f4:
        search = st.text_input("Zoeken in omschrijving",
                               placeholder="bijv. fuse, relay, ...",
                               key="mr_search")

    filtered = combined.copy()
    if multi and pdf_choice != "Alle" and "pdf" in filtered.columns:
        filtered = filtered[filtered["pdf"] == pdf_choice]
    if section_choice and "source_section" in filtered.columns:
        filtered = filtered[filtered["source_section"].isin(section_choice)]
    if status_choice and "match_status" in filtered.columns:
        filtered = filtered[filtered["match_status"].isin(status_choice)]
    if search and "description" in filtered.columns:
        mask = filtered["description"].astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]

    # --- Table ---
    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config=_column_config(filtered),
    )
    st.caption(f"{len(filtered)} van {len(combined)} rijen getoond")

    # --- Exports ---
    st.write("")
    st.markdown("#### Export Match Data")

    all_results = [item["result"] for item in processed_items]
    first_name = pdf_names[0]
    first_stem = Path(first_name).stem
    first_matches = match_results_by_name.get(first_name)

    e1, e2, e3, e4 = st.columns(4)
    with e1:
        if multi or not first_matches or match_xlsx_bytes_fn is None:
            st.button("Match-rapport", disabled=True, use_container_width=True)
            if multi:
                st.caption("Per PDF — upload één tegelijk")
        else:
            st.download_button(
                "Match-rapport",
                data=match_xlsx_bytes_fn(all_results[0], first_matches),
                file_name=f"{first_stem}_match_rapport.xlsx",
                mime=XLSX_MIME,
                use_container_width=True,
                type="primary",
            )
    with e2:
        if multi or not first_matches or niet_gevonden_xlsx_bytes_fn is None:
            st.button("Niet-gevonden lijst", disabled=True, use_container_width=True)
        else:
            st.download_button(
                "Niet-gevonden lijst",
                data=niet_gevonden_xlsx_bytes_fn(all_results[0], first_matches),
                file_name=f"{first_stem}_niet_gevonden.xlsx",
                mime=XLSX_MIME,
                use_container_width=True,
            )
    with e3:
        if multi or procos_bytes_fn is None:
            st.button("ProCos (Excel)", disabled=True, use_container_width=True)
        else:
            st.download_button(
                "ProCos (Excel)",
                data=procos_bytes_fn(all_results[0]),
                file_name=f"{first_stem}_procos.xltm",
                mime=PROCOS_MIME,
                use_container_width=True,
            )
    with e4:
        if multi or procos_xml_bytes_fn is None:
            st.button("ProCos (XML)", disabled=True, use_container_width=True)
        else:
            st.download_button(
                "ProCos (XML)",
                data=procos_xml_bytes_fn(all_results[0]),
                file_name=f"{first_stem}_procos.xml",
                mime=PROCOS_XML_MIME,
                use_container_width=True,
            )
