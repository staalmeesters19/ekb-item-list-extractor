"""Upload screen for the Item-list Extractor."""

import streamlit as st


def render_upload():
    """Render the upload screen.

    Returns list of Streamlit UploadedFile objects (or None if nothing uploaded).
    Also handles the optional ProCos-database upload — stored in session_state
    so it persists across the upload/processing/results stages.
    """
    st.markdown("## Stuklijst-extractie uit PDF-tekeningen")
    st.write(
        "Upload één of meerdere PDF-tekeningen — de tool vindt automatisch "
        "de stuklijst en extraheert elke rij."
    )

    st.write("")

    files = st.file_uploader(
        "PDF-bestanden",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    # Optional second uploader: ProCos artikel-database (one-time per session)
    st.write("")
    with st.expander(
        "⚙️ ProCos-database (optioneel — voor match-rapport)",
        expanded=False,
    ):
        if st.session_state.get("procos_db") is not None:
            st.success(
                f"ProCos-database geladen: "
                f"{st.session_state.get('procos_db_n', '?')} artikelen "
                f"({st.session_state.get('procos_db_name', '')})"
            )
            if st.button("ProCos-database verwijderen", use_container_width=False):
                st.session_state.procos_db = None
                st.session_state.procos_db_n = None
                st.session_state.procos_db_name = None
                st.rerun()
        else:
            st.caption(
                "Upload eenmalig de ProCos-export (Excel met 'export'-blad) — "
                "blijft beschikbaar de hele sessie. Zonder deze stap werkt de "
                "match-knop in het resultatenscherm niet."
            )
            procos_file = st.file_uploader(
                "ProCos-export (.xlsx)",
                type=["xlsx"],
                accept_multiple_files=False,
                label_visibility="collapsed",
                key="procos_uploader",
            )
            if procos_file is not None:
                with st.spinner("ProCos-database inladen..."):
                    try:
                        from backend.pipeline_service import load_procos_db_from_bytes
                        db = load_procos_db_from_bytes(procos_file.getvalue())
                        st.session_state.procos_db = db
                        st.session_state.procos_db_n = db.get("n_rows", "?")
                        st.session_state.procos_db_name = procos_file.name
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Kon ProCos-database niet inladen: {exc}")

    if not files:
        st.write("")
        with st.container(border=True):
            st.markdown("**Ondersteunde scenario's**")
            st.markdown(
                "- Eén PDF-tekening met een stuklijst op één of meerdere pagina's\n"
                "- Meerdere PDF's in batch — elk bestand wordt apart verwerkt\n"
                "- Stuklijsten verdeeld over opeenvolgende pagina's worden samengevoegd"
            )

        st.write("")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Automatisch**")
            st.caption("Classificatie detecteert stuklijstpagina's zonder handmatig werk.")
        with col2:
            st.markdown("**Gestructureerd**")
            st.caption("Rijen komen terug als kolommen: artikel, aantal, omschrijving.")

        return None

    return files
