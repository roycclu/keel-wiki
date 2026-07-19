import streamlit as st
from difflib import unified_diff

from models import (
    PreparedCitationEdit,
    TargetPage
)
from steps import (
    find_citation_needed,
    judge_support_from_sources,
    parser_extract_citation_targets,
    prepare_citation_edit,
    search_web,
    submit_with_citation
)

from pywikibot import config
config.simulate = True


st.set_page_config(
    page_title="Keel Citation Review",
    layout="wide",
)

if st.button("Prepare next review"):
    with st.spinner("Preparing citation review..."):
        pages = find_citation_needed(limit=2)
        if not pages: 
            st.error("No target pages found")
            st.stop()
        
        target_page = pages[0]
        citation_targets = parser_extract_citation_targets(
            target_page,
            150,
            10
        )
        if not citation_targets:
            raise ValueError("No target citations found")

        citation_target = citation_targets[0]
        web_hits = search_web(citation_target)
        if not web_hits:
            raise ValueError("No web hits found")

        decision = judge_support_from_sources(
            citation_target=citation_target,
            web_hits=web_hits
        )

        if not decision.supports_claim or decision.evidence_index is None:
            st.warning("No supporting evidence was found")
            st.stop()
        
        prepared_edit = prepare_citation_edit(
            target_page,
            citation_target,
            web_hits[decision.evidence_index]
        )

        st.session_state.target_page = target_page
        st.session_state.prepared_edit = prepared_edit
        st.session_state.explanation = decision.explanation

target_page = st.session_state.get("target_page")
prepared_edit = st.session_state.get("prepared_edit")

if target_page and prepared_edit:
    st.subheader(target_page.title)
    st.write(st.session_state.explanation)

    st.subheader("Generated Citation")
    st.code(prepared_edit.citation, language=None, wrap_lines=True)

    diff = "".join(
        unified_diff(
            prepared_edit.original_wikitext.splitlines(keepends=True),
            prepared_edit.new_wikitext.splitlines(keepends=True),
            fromfile="Current Wikipedia text",
            tofile="Proposed Wikipedia text"
        )
    )

    st.subheader("Proposed change")
    st.code(diff, language="diff", wrap_lines=True)

    if st.button("Save citation", type="primary"):
        submit_with_citation(target_page, prepared_edit)
