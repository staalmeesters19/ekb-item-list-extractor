"""Upload screen for the Item-list Extractor."""

import streamlit as st


def render_upload():
    """Render the upload screen.

    Returns list of Streamlit UploadedFile objects (or None if nothing uploaded).
    """
    st.markdown("# Item-list Extractor")
    st.caption("Automatische stuklijst-extractie uit PDF-tekeningen")

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
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Automatisch**")
            st.caption("Classificatie detecteert stuklijstpagina's zonder handmatig werk.")
        with col2:
            st.markdown("**Gestructureerd**")
            st.caption("Rijen komen terug als kolommen: artikel, aantal, omschrijving.")
        with col3:
            st.markdown("**Exporteerbaar**")
            st.caption("Download als Excel, CSV of JSON voor verdere verwerking.")

        return None

    return files
