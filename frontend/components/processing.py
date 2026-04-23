"""Processing screen: classify + extract each uploaded PDF with live status."""

import tempfile

import streamlit as st


def _format_page_runs(page_runs):
    """Render page runs like [[3,4],[7]] as '3-4, 7'."""
    parts = []
    for run in page_runs:
        if not run:
            continue
        if len(run) == 1:
            parts.append(str(run[0]))
        else:
            parts.append(f"{run[0]}-{run[-1]}")
    return ", ".join(parts) if parts else "—"


def render_processing(files, classify_fn, extract_fn):
    """Process each uploaded file, show live status.

    Args:
        files: list of UploadedFile
        classify_fn: callable(pdf_path: str) -> list[list[int]]
        extract_fn:  callable(pdf_path: str, page_runs: list[list[int]]) -> ExtractionResult

    Returns list of dicts: [{"name": str, "path": str, "page_runs": [...], "result": ExtractionResult}, ...]
    """
    processed = []

    st.markdown("### Verwerking")
    st.caption(f"{len(files)} bestand(en) in de wachtrij")
    st.write("")

    for uploaded in files:
        # Persist upload to a temp file so backend functions can read it by path.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name

        with st.status(f"Verwerken: {uploaded.name}", expanded=True) as status:
            try:
                st.write("Pagina's classificeren...")
                page_runs = classify_fn(tmp_path)

                flat_pages = sorted({p for run in page_runs for p in run})
                st.write(
                    f"→ {len(page_runs)} stuklijst(en) gevonden op pagina's "
                    f"{_format_page_runs(page_runs)}"
                )

                if not page_runs:
                    status.update(
                        label=f"Geen stuklijst gevonden: {uploaded.name}",
                        state="error",
                    )
                    continue

                st.write("Rijen extraheren...")
                result = extract_fn(tmp_path, page_runs)
                st.write(f"→ {result.row_count} rijen")

                processed.append(
                    {
                        "name": uploaded.name,
                        "path": tmp_path,
                        "page_runs": page_runs,
                        "result": result,
                    }
                )

                status.update(label=f"Klaar: {uploaded.name}", state="complete")

            except Exception as e:  # noqa: BLE001 — surface any backend failure to user
                status.update(
                    label=f"Fout bij verwerken: {uploaded.name}",
                    state="error",
                )
                st.exception(e)
                continue

    return processed
