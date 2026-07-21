from pprint import pprint
import streamlit as st
from difflib import unified_diff
from random import choice

from models import CitationSubmissionResult, PreparedCitationEdit, TargetPage
from steps import (
    find_citation_needed,
    pick_one_probable_citation_target,
    judge_support_from_sources_by_llm,
    parser_extract_citation_targets,
    prepare_citation_edit,
    search_web_for_citation_support,
    submit_with_citation,
)

from pywikibot import config

config.simulate = True


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

        target_page = choice(pages)

        citation_targets = parser_extract_citation_targets(target_page, 250, 10)

        if not citation_targets:
            st.warning("No target citations were found")
            st.stop()
        citation_target = pick_one_probable_citation_target(citation_targets)
        print("Chosen citation target")
        pprint(citation_target.original_template)
        pprint(citation_target.marker)
        pprint(citation_target.context)

        web_hits = search_web_for_citation_support(citation_target, limit=10)
        print("Next web hits")
        pprint(web_hits)
        if not web_hits:
            st.warning("No web search results were found")
            st.stop()

        decision = judge_support_from_sources_by_llm(
            citation_target=citation_target, web_hits=web_hits
        )

    if not decision.supports_claim or decision.evidence_index is None:
        st.warning("No supporting evidence was found")
        st.warning(decision)
        st.stop()

    prepared_edit = prepare_citation_edit(
        target_page, citation_target, web_hits[decision.evidence_index]
    )

    st.session_state.target_page = target_page
    st.session_state.prepared_edit = prepared_edit
    st.session_state.explanation = decision.explanation

target_page: TargetPage | None = st.session_state.get("target_page")
prepared_edit: PreparedCitationEdit | None = st.session_state.get("prepared_edit")
submission_result: CitationSubmissionResult | None = st.session_state.get(
    "submission_result"
)

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
            tofile="Proposed Wikipedia text",
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
