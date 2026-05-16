"""Results screen: metrics, filters, table, and 2 downstream actions
(Match tegen ProCos + Download Excel)."""

from pathlib import Path

import pandas as pd
import streamlit as st

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _collect_metrics(processed_items):
    total_rows = 0
    pages = set()
    sections = set()
    warnings_count = 0
    for item in processed_items:
        result = item["result"]
        total_rows += getattr(result, "row_count", len(getattr(result, "rows", [])))
        for row in getattr(result, "rows", []):
            page = getattr(row, "source_page", None)
            if page is not None:
                pages.add((item["name"], page))
            section = getattr(row, "source_section", None)
            if section:
                sections.add(section)
            if getattr(row, "warnings", None):
                warnings_count += 1
    return total_rows, len(pages), sorted(sections), warnings_count


def _build_combined_df(processed_items, rows_to_df_fn):
    frames = []
    for item in processed_items:
        df = rows_to_df_fn(item["result"])
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, "pdf", item["name"])
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
        else:
            cfg[col] = st.column_config.TextColumn(col, width="medium")
    return cfg


def render_results(
    processed_items,
    rows_to_df_fn,
    raw_xlsx_bytes_fn,
):
    """Render results screen.

    Bottom of the page has exactly two action buttons:
      1. "Match tegen ProCos" — switches to the matching stage
      2. "Download Excel" — raw extraction Excel
    """
    if not processed_items:
        st.info("Geen resultaten om te tonen.")
        return

    total_rows, unique_pages, unique_sections, warnings_count = _collect_metrics(processed_items)

    st.markdown("### Resultaten")
    st.write("")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Totaal rijen", total_rows)
    m2.metric("Pagina's verwerkt", unique_pages)
    m3.metric("Aantal secties", len(unique_sections))
    m4.metric("Warnings", warnings_count)

    st.write("")

    combined = _build_combined_df(processed_items, rows_to_df_fn)
    pdf_names = [item["name"] for item in processed_items]
    multi = len(processed_items) > 1

    f1, f2, f3 = st.columns([2, 2, 3])
    with f1:
        if multi:
            pdf_choice = st.selectbox("PDF", ["Alle"] + pdf_names, index=0)
        else:
            pdf_choice = "Alle"
            st.text_input("PDF", value=pdf_names[0], disabled=True)
    with f2:
        section_choice = st.multiselect("Secties", unique_sections)
    with f3:
        search = st.text_input("Zoeken in omschrijving", placeholder="bijv. fuse, relay, ...")

    filtered = combined.copy()
    if multi and pdf_choice != "Alle" and "pdf" in filtered.columns:
        filtered = filtered[filtered["pdf"] == pdf_choice]
    if section_choice and "source_section" in filtered.columns:
        filtered = filtered[filtered["source_section"].isin(section_choice)]
    if search and "description" in filtered.columns:
        mask = filtered["description"].astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]

    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config=_column_config(filtered),
    )
    st.caption(f"{len(filtered)} van {len(combined)} rijen getoond")

    # --- Two action buttons at the bottom ---
    st.write("")
    all_results = [item["result"] for item in processed_items]
    first_stem = Path(processed_items[0]["name"]).stem
    raw_name = "extractie.xlsx" if multi else f"{first_stem}_extractie.xlsx"

    procos_db_loaded = st.session_state.get("procos_db") is not None

    b1, b2 = st.columns(2)
    with b1:
        match_disabled = not procos_db_loaded
        if st.button(
            "Match tegen ProCos",
            type="primary",
            use_container_width=True,
            disabled=match_disabled,
        ):
            # Clear any previous match results — entering matching stage fresh
            st.session_state.match_results_by_name = None
            st.session_state.stage = "matching"
            st.rerun()
        if match_disabled:
            st.caption(
                "Upload eerst de ProCos-database in het upload-scherm "
                "(⚙️-expander) om te kunnen matchen."
            )
    with b2:
        if raw_xlsx_bytes_fn is None:
            st.button("Download Excel", disabled=True, use_container_width=True)
        else:
            st.download_button(
                "Download Excel",
                data=raw_xlsx_bytes_fn(all_results),
                file_name=raw_name,
                mime=XLSX_MIME,
                use_container_width=True,
            )
