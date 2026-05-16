"""Matching screen — loading state between Results and Match-results.

Triggered when the user clicks 'Match tegen ProCos' on the results screen.
Runs the match, stores results in session_state, then offers a button to
proceed to the match-results screen.
"""

from __future__ import annotations

import streamlit as st


def render_matching(processed_items, run_match_fn):
    """Render the loading + transition screen.

    Args:
        processed_items: list of {"name": str, "result": ExtractionResult, ...}
        run_match_fn: callable(result, db) -> List[MatchResult]
    """
    st.markdown("### Match tegen ProCos")
    st.write("")

    procos_db = st.session_state.get("procos_db")
    if procos_db is None:
        st.error(
            "Geen ProCos-database geladen. Ga terug naar het upload-scherm en "
            "upload eerst de ProCos-export."
        )
        if st.button("← Terug naar resultaten", use_container_width=False):
            st.session_state.stage = "results"
            st.rerun()
        return

    # If results haven't been computed yet, run the match and rerun.
    if not st.session_state.get("match_results_by_name"):
        with st.spinner(
            f"Bezig met matchen van {len(processed_items)} PDF('s) tegen "
            f"{st.session_state.get('procos_db_n', '?')} ProCos-artikelen..."
        ):
            results_by_name = {}
            for item in processed_items:
                results_by_name[item["name"]] = run_match_fn(item["result"], procos_db)
            st.session_state.match_results_by_name = results_by_name
        st.rerun()
        return

    # Match is done — show summary + continue button
    match_results_by_name = st.session_state.match_results_by_name
    total = 0
    matched = 0
    for v in match_results_by_name.values():
        for m in v:
            total += 1
            if m.status.startswith("MATCH"):
                matched += 1
    pct = (100 * matched / total) if total else 0.0

    st.success(
        f"✓ Match voltooid — {matched}/{total} rijen gematched ({pct:.1f}%)"
    )
    st.write("")

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Ga naar resultaten →", type="primary", use_container_width=True):
            st.session_state.stage = "match_results"
            st.rerun()
    with c2:
        if st.button("← Terug", use_container_width=False):
            st.session_state.match_results_by_name = None
            st.session_state.stage = "results"
            st.rerun()
