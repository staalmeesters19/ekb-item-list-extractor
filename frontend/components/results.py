"""Results screen: metrics, filters, table, match-step and downloads."""

from pathlib import Path

import pandas as pd
import streamlit as st

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"
JSON_MIME = "application/json"
PROCOS_MIME = "application/vnd.ms-excel.template.macroEnabled.12"
PROCOS_XML_MIME = "text/xml"

# Feature flag: hide the ProCos download in the demo build.
# Backend (procos_writer, to_procos_bytes, CLI --format procos) stays intact.
# Flip to True to restore the button.
SHOW_PROCOS_DOWNLOAD = True


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


def _build_combined_df(processed_items, rows_to_df_fn, match_results_by_name=None):
    """Build combined DataFrame across all processed items.

    If match_results_by_name is provided (dict name -> List[MatchResult]),
    appends match_status, procos_artikel, procos_omschrijving columns.
    """
    frames = []
    for item in processed_items:
        df = rows_to_df_fn(item["result"])
        if df is None or df.empty:
            continue
        df = df.copy()
        df.insert(0, "pdf", item["name"])
        if match_results_by_name and item["name"] in match_results_by_name:
            matches = match_results_by_name[item["name"]]
            if len(matches) == len(df):
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


def _summarize_match_results(match_results):
    """Aggregate stats across a single PDF's match-results."""
    by_status = {}
    for m in match_results:
        by_status[m.status] = by_status.get(m.status, 0) + 1
    match_count = sum(c for s, c in by_status.items() if s.startswith("MATCH"))
    total = len(match_results)
    niet_gev = sum(c for s, c in by_status.items() if s.startswith("NIET GEVONDEN"))
    niet_uniek = sum(c for s, c in by_status.items() if s.startswith("NIET UNIEK"))
    return {
        "total": total,
        "match": match_count,
        "niet_gevonden": niet_gev,
        "niet_uniek": niet_uniek,
        "match_pct": (100 * match_count / total) if total else 0.0,
    }


def render_results(
    processed_items,
    rows_to_df_fn,
    xlsx_bytes_fn,
    csv_bytes_fn,
    json_bytes_fn,
    procos_bytes_fn=None,
    procos_xml_bytes_fn=None,
    raw_xlsx_bytes_fn=None,
    run_match_fn=None,
    match_xlsx_bytes_fn=None,
    niet_gevonden_xlsx_bytes_fn=None,
):
    """Render results screen."""
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

    # --- Match-step block ----------------------------------------------------
    procos_db = st.session_state.get("procos_db")
    match_results_by_name = st.session_state.get("match_results_by_name") or {}

    mc1, mc2 = st.columns([2, 1])
    with mc1:
        if procos_db is None:
            st.info(
                "ℹ️ Geen ProCos-database geladen. Ga terug naar het upload-scherm en upload "
                "de ProCos-export om te kunnen matchen tegen de artikeldatabase."
            )
        elif not match_results_by_name:
            st.caption(
                f"ProCos-database geladen ({st.session_state.get('procos_db_n', '?')} artikelen). "
                f"Klik op de knop om elke rij tegen ProCos te matchen."
            )
        else:
            total_matched = sum(_summarize_match_results(v)["match"] for v in match_results_by_name.values())
            total_all = sum(_summarize_match_results(v)["total"] for v in match_results_by_name.values())
            pct = (100 * total_matched / total_all) if total_all else 0.0
            st.caption(f"✓ Match uitgevoerd — {total_matched}/{total_all} rijen gematched ({pct:.1f}%)")
    with mc2:
        match_disabled = procos_db is None or run_match_fn is None
        match_label = "Hermatch" if match_results_by_name else "Match tegen ProCos"
        if st.button(match_label, disabled=match_disabled, use_container_width=True, type="primary"):
            with st.spinner("Bezig met matchen..."):
                results_by_name = {}
                for item in processed_items:
                    results_by_name[item["name"]] = run_match_fn(item["result"], procos_db)
                st.session_state.match_results_by_name = results_by_name
            st.rerun()

    # If matches are available, show the match-status metric row
    if match_results_by_name:
        agg = {"total": 0, "match": 0, "niet_gevonden": 0, "niet_uniek": 0}
        for v in match_results_by_name.values():
            s = _summarize_match_results(v)
            for k in agg:
                agg[k] += s[k]
        match_pct = (100 * agg["match"] / agg["total"]) if agg["total"] else 0.0

        st.write("")
        mm1, mm2, mm3, mm4 = st.columns(4)
        mm1.metric("Match %", f"{match_pct:.1f}%")
        mm2.metric("MATCH", agg["match"])
        mm3.metric("NIET GEVONDEN", agg["niet_gevonden"])
        mm4.metric("NIET UNIEK", agg["niet_uniek"])

    st.write("")

    combined = _build_combined_df(processed_items, rows_to_df_fn, match_results_by_name)
    pdf_names = [item["name"] for item in processed_items]
    multi = len(processed_items) > 1

    # --- Filters --------------------------------------------------------------
    has_match = "match_status" in combined.columns
    if has_match:
        f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    else:
        f1, f2, f3 = st.columns([2, 2, 3])
        f4 = None

    with f1:
        if multi:
            pdf_choice = st.selectbox("PDF", ["Alle"] + pdf_names, index=0)
        else:
            pdf_choice = "Alle"
            st.text_input("PDF", value=pdf_names[0], disabled=True)
    with f2:
        section_choice = st.multiselect("Secties", unique_sections)
    if has_match:
        with f3:
            status_options = sorted(combined["match_status"].dropna().unique().tolist())
            status_choice = st.multiselect("Match-status", status_options)
        with f4:
            search = st.text_input("Zoeken in omschrijving", placeholder="bijv. fuse, relay, ...")
    else:
        status_choice = []
        with f3:
            search = st.text_input("Zoeken in omschrijving", placeholder="bijv. fuse, relay, ...")

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

    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config=_column_config(filtered),
    )
    st.caption(f"{len(filtered)} van {len(combined)} rijen getoond")

    # --- Downloads -----------------------------------------------------------
    if SHOW_PROCOS_DOWNLOAD:
        st.write("")

        all_results = [item["result"] for item in processed_items]
        first_stem = Path(processed_items[0]["name"]).stem

        # Row 1: 3 ProCos / raw exports
        col1, col2, col3 = st.columns(3)
        with col1:
            if raw_xlsx_bytes_fn is None:
                st.button("Download Rauwe Excel", disabled=True, use_container_width=True)
            else:
                raw_name = "extractie.xlsx" if multi else f"{first_stem}_extractie.xlsx"
                st.download_button(
                    "Download Rauwe Excel",
                    data=raw_xlsx_bytes_fn(all_results),
                    file_name=raw_name,
                    mime=XLSX_MIME,
                    use_container_width=True,
                )
        with col2:
            if multi or procos_bytes_fn is None:
                st.button("Download ProCos (Excel)", disabled=True, use_container_width=True)
                if multi:
                    st.caption("ProCos-export per PDF — upload één tegelijk")
            else:
                st.download_button(
                    "Download ProCos (Excel)",
                    data=procos_bytes_fn(all_results[0]),
                    file_name=f"{first_stem}_procos.xltm",
                    mime=PROCOS_MIME,
                    use_container_width=True,
                )
        with col3:
            if multi or procos_xml_bytes_fn is None:
                st.button("Download ProCos (XML)", disabled=True, use_container_width=True)
            else:
                st.download_button(
                    "Download ProCos (XML)",
                    data=procos_xml_bytes_fn(all_results[0]),
                    file_name=f"{first_stem}_procos.xml",
                    mime=PROCOS_XML_MIME,
                    use_container_width=True,
                    type="primary",
                )

        # Row 2: match exports (only show when match has been run)
        if match_results_by_name:
            st.write("")
            mc1, mc2 = st.columns(2)
            with mc1:
                if multi or match_xlsx_bytes_fn is None:
                    st.button("Download Match-rapport", disabled=True, use_container_width=True)
                else:
                    matches = match_results_by_name.get(pdf_names[0])
                    if matches:
                        st.download_button(
                            "Download Match-rapport",
                            data=match_xlsx_bytes_fn(all_results[0], matches),
                            file_name=f"{first_stem}_match_rapport.xlsx",
                            mime=XLSX_MIME,
                            use_container_width=True,
                        )
                    else:
                        st.button("Download Match-rapport", disabled=True, use_container_width=True)
            with mc2:
                if multi or niet_gevonden_xlsx_bytes_fn is None:
                    st.button("Download Niet-gevonden lijst", disabled=True, use_container_width=True)
                else:
                    matches = match_results_by_name.get(pdf_names[0])
                    if matches:
                        st.download_button(
                            "Download Niet-gevonden lijst",
                            data=niet_gevonden_xlsx_bytes_fn(all_results[0], matches),
                            file_name=f"{first_stem}_niet_gevonden.xlsx",
                            mime=XLSX_MIME,
                            use_container_width=True,
                        )
                    else:
                        st.button("Download Niet-gevonden lijst", disabled=True, use_container_width=True)
