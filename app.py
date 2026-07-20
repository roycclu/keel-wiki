from pprint import pprint
import streamlit as st
from difflib import unified_diff

from models import (
    CitationSubmissionResult,
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
config.simulate = False


st.set_page_config(
    page_title="Keel Citation Review",
    layout="wide",
)

def clear():
    st.session_state.pop("target_page", None)
    st.session_state.pop("prepared_edit", None)
    st.session_state.pop("submission_result", None)

    st.session_state.pop("warning", None)

if st.button("Prepare next review"):

    clear()

    with st.spinner("Preparing citation review..."):
        pages = find_citation_needed(limit=2)
        if not pages: 
            st.error("No target pages found")
            st.stop()
        
        target_page = pages[0]
        citation_targets = parser_extract_citation_targets(
            target_page,
            250,
            10
        )
       
        if not citation_targets:
            raise ValueError("No target citations found")
        citation_target = citation_targets[0]
        print("Next citation target")
        pprint(citation_target.original_template)
        pprint(citation_target.marker)
        pprint(citation_target.context)

        web_hits = search_web(citation_target)
        print("Next web hits")
        pprint(web_hits)
        if not web_hits:
            raise ValueError("No web hits found")
        

        decision = judge_support_from_sources(
            citation_target=citation_target,
            web_hits=web_hits
        )

    if not decision.supports_claim or decision.evidence_index is None:
        st.warning("No supporting evidence was found")
        st.warning(decision)
        st.stop()
    
    prepared_edit = prepare_citation_edit(
        target_page,
        citation_target,
        web_hits[decision.evidence_index]
    )

    st.session_state.target_page = target_page
    st.session_state.prepared_edit = prepared_edit
    st.session_state.explanation = decision.explanation

target_page: TargetPage | None = st.session_state.get("target_page")
prepared_edit: PreparedCitationEdit | None = st.session_state.get("prepared_edit")
submission_result: CitationSubmissionResult | None = st.session_state.get("submission_result")

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

    if st.button(
        "Submit citation", 
        type="primary",
        disabled=submission_result is not None,
    ):
        with st.spinner("Submitting citation..."):
            try:
                submission_result = submit_with_citation(target_page, prepared_edit)
            except Exception as e:
                st.exception(e)
            else:
                st.session_state.submission_result = submission_result
                st.session_state.pop("target_page", None)
                st.session_state.pop("prepared_edit", None)
                st.rerun()

if submission_result is not None and submission_result.success:
    if not submission_result.production:
        st.info("Simulation completed. No Wikipedia edit was saved.")

    else:
        st.success(f"Wikipedia revision {submission_result.revision_id} was saved.")
        if submission_result.revision_url is not None:
            st.link_button(
                "View saved revision",
                str(submission_result.revision_url),
            )
