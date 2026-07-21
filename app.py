import time
from difflib import unified_diff

import streamlit as st
from pywikibot import config

from models import (
    CitationSubmissionResult,
    CitationTarget,
    DecisionCitationSupport,
    PreparedCitationEdit,
    TargetPage,
    WebSearchEvidence,
)
from steps import (
    find_citation_needed,
    judge_support_from_sources_by_llm,
    parser_extract_citation_targets,
    prepare_citation_edit,
    search_web_for_citation_support,
    submit_with_citation,
)

config.simulate = True

WORKFLOW_STEPS = (
    ("Select a page", "Discover Wikipedia pages and choose one to review."),
    ("Select a claim", "Choose one missing citation from the selected page."),
    ("Find sources", "Search the web for possible supporting evidence."),
    ("Verify evidence", "Choose sources and ask the agent to judge support."),
    ("Prepare the edit", "Generate the citation and proposed Wikipedia diff."),
    ("Review and submit", "Approve the final edit before submission."),
)

SESSION_KEYS = (
    "page_candidates",
    "target_page",
    "citation_targets",
    "citation_target",
    "web_hits",
    "review_web_hits",
    "decision",
    "prepared_edit",
    "submission_result",
    "workflow_stage",
    "workflow_error",
)

st.set_page_config(
    page_title="Keel Citation Review",
    page_icon="⚓",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1500px;
        padding-top: 2rem;
        padding-bottom: 4rem;
    }
    div[data-testid="stCode"] pre {
        max-height: 34rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def clear_review() -> None:
    """Remove one review run while preserving presentation settings."""
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


def build_diff(prepared_edit: PreparedCitationEdit) -> str:
    """Create the reviewable unified diff for a prepared edit."""
    return "".join(
        unified_diff(
            prepared_edit.original_wikitext.splitlines(keepends=True),
            prepared_edit.new_wikitext.splitlines(keepends=True),
            fromfile="Current Wikipedia text",
            tofile="Proposed Wikipedia text",
        )
    )


def render_workflow(
    placeholder: st.delta_generator.DeltaGenerator,
    completed_steps: int,
    error: str | None = None,
) -> None:
    """Render the current workflow state inside a replaceable panel."""
    placeholder.empty()
    with placeholder.container(border=True):
        st.subheader("Workflow")
        for index, (label, description) in enumerate(WORKFLOW_STEPS):
            if index < completed_steps:
                icon = "✅"
            elif index == completed_steps and completed_steps < len(WORKFLOW_STEPS):
                icon = "⏳"
            else:
                icon = "○"

            st.markdown(f"{icon} **{index + 1}. {label}**")
            st.caption(description)

        if error:
            st.error(error)


def pause_for_review(delay_seconds: float) -> None:
    """Hold a completed stage briefly so its output is readable."""
    time.sleep(delay_seconds)


def fail_workflow(
    placeholder: st.delta_generator.DeltaGenerator,
    error: Exception,
) -> None:
    """Persist and display a workflow error without losing prior choices."""
    message = str(error)
    st.session_state.workflow_error = message
    render_workflow(
        placeholder,
        st.session_state.workflow_stage,
        message,
    )
    st.exception(error)


def show_page(target_page: TargetPage) -> None:
    """Show the selected Wikipedia page."""
    with st.container(border=True):
        st.caption("SELECTED WIKIPEDIA PAGE")
        st.subheader(target_page.title)
        st.link_button("Open Wikipedia page", target_page.url)


def show_claim(citation_target: CitationTarget) -> None:
    """Show the claim context selected for citation research."""
    with st.container(border=True):
        st.caption("CLAIM NEEDING A CITATION")
        st.write(citation_target.context.strip())
        st.caption(f"Wikipedia marker: {citation_target.original_template}")


def source_label(index: int, web_hits: list[WebSearchEvidence]) -> str:
    """Create a compact label for a selectable source."""
    return f"{index + 1}. {web_hits[index].title}"


def target_label(index: int, citation_targets: list[CitationTarget]) -> str:
    """Create a compact label for a selectable citation target."""
    context = " ".join(citation_targets[index].context.split())
    if len(context) > 180:
        context = f"{context[:177]}..."
    return f"{index + 1}. {context}"


def show_sources(
    web_hits: list[WebSearchEvidence], selected_index: int | None = None
) -> None:
    """Show evidence cards and identify the agent-selected source."""
    for index, hit in enumerate(web_hits):
        selected = index == selected_index
        label = f"{'✓ ' if selected else ''}{index + 1}. {hit.title}"
        with st.expander(label, expanded=selected or index == 0):
            st.caption(str(hit.url))
            if hit.description:
                st.write(hit.description)
            for snippet in hit.extra_snippets:
                st.info(snippet)
            st.link_button("Read source", str(hit.url), key=f"source-{index}")


def show_decision(
    decision: DecisionCitationSupport, web_hits: list[WebSearchEvidence]
) -> None:
    """Show the evidence judgment and selected supporting source."""
    with st.container(border=True):
        st.caption("AGENT EVIDENCE DECISION")
        if decision.supports_claim and decision.evidence_index is not None:
            st.success("A source directly supporting the claim was found.")
            st.write(decision.explanation)
            selected_hit = web_hits[decision.evidence_index]
            st.write("Selected source:", selected_hit.title)
            st.link_button("Open selected source", str(selected_hit.url))
        else:
            st.warning("The selected sources did not directly support the claim.")
            st.write(decision.explanation)


def show_prepared_edit(prepared_edit: PreparedCitationEdit) -> None:
    """Show the generated citation and its exact proposed change."""
    citation_tab, diff_tab = st.tabs(("Generated citation", "Proposed diff"))
    with citation_tab:
        st.code(prepared_edit.citation, language=None, wrap_lines=True)
    with diff_tab:
        st.code(build_diff(prepared_edit), language="diff", wrap_lines=True)


if "workflow_stage" not in st.session_state:
    st.session_state.workflow_stage = 0

st.title("Keel Citation Review")
st.caption(
    "Choose the page, claim, and evidence before asking the agent to prepare an edit."
)

workflow_column, content_column = st.columns((0.85, 2.15), gap="large")

with workflow_column:
    presentation_delay = st.slider(
        "Step pacing",
        min_value=0.5,
        max_value=3.0,
        value=1.0,
        step=0.5,
        help="Pause after machine work so each completed stage is easier to follow.",
    )

    has_review = any(key in st.session_state for key in SESSION_KEYS[:-2])
    if st.button(
        "Start over",
        disabled=not has_review,
        use_container_width=True,
    ):
        clear_review()
        st.session_state.workflow_stage = 0
        st.rerun()

    workflow_placeholder = st.empty()

render_workflow(
    workflow_placeholder,
    st.session_state.workflow_stage,
    st.session_state.get("workflow_error"),
)

with content_column:
    page_candidates: list[TargetPage] | None = st.session_state.get("page_candidates")
    target_page: TargetPage | None = st.session_state.get("target_page")
    citation_targets: list[CitationTarget] | None = st.session_state.get(
        "citation_targets"
    )
    citation_target: CitationTarget | None = st.session_state.get("citation_target")
    web_hits: list[WebSearchEvidence] | None = st.session_state.get("web_hits")
    review_web_hits: list[WebSearchEvidence] | None = st.session_state.get(
        "review_web_hits"
    )
    decision: DecisionCitationSupport | None = st.session_state.get("decision")
    prepared_edit: PreparedCitationEdit | None = st.session_state.get("prepared_edit")
    submission_result: CitationSubmissionResult | None = st.session_state.get(
        "submission_result"
    )

    if page_candidates is None:
        with st.container(border=True):
            st.subheader("1. Discover pages")
            st.write(
                "Retrieve a small set of Wikipedia pages containing missing "
                "citation markers, then choose which page to investigate."
            )
            st.info("Simulation mode is on. Wikipedia edits will not be saved.")

            if st.button("Find Wikipedia pages", type="primary"):
                try:
                    with st.status("Finding Wikipedia pages...", expanded=True) as status:
                        page_candidates = find_citation_needed(limit=5)
                        if not page_candidates:
                            raise RuntimeError("No target pages were found.")
                        st.session_state.page_candidates = page_candidates
                        st.session_state.workflow_error = None
                        status.update(
                            label=f"Found {len(page_candidates)} candidate pages",
                            state="complete",
                        )
                        pause_for_review(presentation_delay)
                    st.rerun()
                except Exception as error:
                    fail_workflow(workflow_placeholder, error)

    elif target_page is None:
        st.subheader("1. Select a Wikipedia page")
        st.write("Keel found these pages. Choose one before extracting its claims.")

        with st.form("page-selection-form"):
            selected_page_index = st.radio(
                "Candidate pages",
                options=list(range(len(page_candidates))),
                format_func=lambda index: page_candidates[index].title,
            )
            select_page = st.form_submit_button(
                "Use selected page",
                type="primary",
                use_container_width=True,
            )

        for page in page_candidates:
            st.page_link(page.url, label=page.title, icon="↗️")

        if select_page:
            try:
                selected_page = page_candidates[selected_page_index]
                with st.status(
                    f"Extracting missing citations from {selected_page.title}...",
                    expanded=True,
                ) as status:
                    citation_targets = parser_extract_citation_targets(
                        selected_page,
                        250,
                        10,
                    )
                    if not citation_targets:
                        raise RuntimeError(
                            "No citation targets were found on the selected page."
                        )
                    st.session_state.target_page = selected_page
                    st.session_state.citation_targets = citation_targets
                    st.session_state.workflow_stage = 1
                    st.session_state.workflow_error = None
                    show_page(selected_page)
                    status.update(
                        label=f"Found {len(citation_targets)} missing citations",
                        state="complete",
                    )
                    render_workflow(workflow_placeholder, 1)
                    pause_for_review(presentation_delay)
                st.rerun()
            except Exception as error:
                fail_workflow(workflow_placeholder, error)

    elif citation_target is None and citation_targets is not None:
        show_page(target_page)
        st.subheader("2. Select a claim")
        st.write(
            "These are the missing citation markers found on the page. "
            "Choose the claim you want Keel to research."
        )

        with st.form("citation-selection-form"):
            selected_target_index = st.radio(
                "Claims needing citations",
                options=list(range(len(citation_targets))),
                format_func=lambda index: target_label(index, citation_targets),
            )
            select_target = st.form_submit_button(
                "Research selected claim",
                type="primary",
                use_container_width=True,
            )

        if select_target:
            selected_target = citation_targets[selected_target_index]
            st.session_state.citation_target = selected_target
            st.session_state.workflow_stage = 2
            st.session_state.workflow_error = None
            render_workflow(workflow_placeholder, 2)
            show_claim(selected_target)
            pause_for_review(presentation_delay)
            st.rerun()

    elif web_hits is None and citation_target is not None:
        show_page(target_page)
        show_claim(citation_target)
        st.subheader("3. Find potential sources")
        st.write(
            "Search the web using the selected claim. You will inspect and choose "
            "the results before the agent evaluates them."
        )

        if st.button("Search for supporting sources", type="primary"):
            try:
                with st.status(
                    "Searching for supporting sources...",
                    expanded=True,
                ) as status:
                    web_hits = search_web_for_citation_support(
                        citation_target,
                        limit=10,
                    )
                    if not web_hits:
                        raise RuntimeError(
                            "No web results with supporting snippets were found."
                        )
                    st.session_state.web_hits = web_hits
                    st.session_state.workflow_stage = 3
                    st.session_state.workflow_error = None
                    status.update(
                        label=f"Found {len(web_hits)} potential sources",
                        state="complete",
                    )
                    render_workflow(workflow_placeholder, 3)
                    pause_for_review(presentation_delay)
                st.rerun()
            except Exception as error:
                fail_workflow(workflow_placeholder, error)

    elif prepared_edit is None and web_hits is not None:
        show_page(target_page)
        show_claim(citation_target)

        if decision is not None and review_web_hits is not None:
            show_decision(decision, review_web_hits)

        st.subheader("4. Choose evidence for agent review")
        st.write(
            "Inspect the search results, select the credible candidates, and then "
            "ask the agent whether any selected source directly supports the claim."
        )
        show_sources(web_hits)

        with st.form("source-selection-form"):
            selected_source_indexes = st.multiselect(
                "Sources to send to the agent",
                options=list(range(len(web_hits))),
                default=list(range(len(web_hits))),
                format_func=lambda index: source_label(index, web_hits),
            )
            verify_sources = st.form_submit_button(
                "Ask agent to verify selected sources",
                type="primary",
                use_container_width=True,
            )

        if verify_sources:
            if not selected_source_indexes:
                st.warning("Select at least one source for the agent to review.")
            else:
                selected_hits = [web_hits[index] for index in selected_source_indexes]
                try:
                    with st.status(
                        "Asking the agent to verify citation support...",
                        expanded=True,
                    ) as status:
                        decision = judge_support_from_sources_by_llm(
                            citation_target=citation_target,
                            web_hits=selected_hits,
                        )
                        st.session_state.review_web_hits = selected_hits
                        st.session_state.decision = decision
                        st.session_state.workflow_error = None

                        if (
                            not decision.supports_claim
                            or decision.evidence_index is None
                        ):
                            status.update(
                                label="The selected sources did not support the claim",
                                state="error",
                            )
                            pause_for_review(presentation_delay)
                            st.rerun()

                        st.session_state.workflow_stage = 4
                        render_workflow(workflow_placeholder, 4)
                        show_decision(decision, selected_hits)
                        pause_for_review(presentation_delay)

                        prepared_edit = prepare_citation_edit(
                            target_page,
                            citation_target,
                            selected_hits[decision.evidence_index],
                        )
                        st.session_state.prepared_edit = prepared_edit
                        st.session_state.workflow_stage = 5
                        status.update(
                            label="Evidence verified and edit prepared",
                            state="complete",
                        )
                        render_workflow(workflow_placeholder, 5)
                        pause_for_review(presentation_delay)
                    st.rerun()
                except Exception as error:
                    fail_workflow(workflow_placeholder, error)

    elif prepared_edit is not None:
        show_page(target_page)
        show_claim(citation_target)

        if decision is not None and review_web_hits is not None:
            show_decision(decision, review_web_hits)
            with st.expander("Sources reviewed by the agent"):
                show_sources(review_web_hits, decision.evidence_index)

        st.subheader("5. Review the proposed edit")
        show_prepared_edit(prepared_edit)

        if submission_result is None:
            st.warning(
                "This is the final human approval. Review the source and diff before "
                "submitting the edit."
            )
            if st.button(
                "Approve and submit citation",
                type="primary",
                use_container_width=True,
            ):
                with st.status(
                    "Submitting the approved edit...",
                    expanded=True,
                ) as status:
                    try:
                        submission_result = submit_with_citation(
                            target_page,
                            prepared_edit,
                        )
                    except Exception as error:
                        status.update(label="Submission failed", state="error")
                        st.exception(error)
                    else:
                        st.session_state.submission_result = submission_result
                        st.session_state.workflow_stage = len(WORKFLOW_STEPS)
                        status.update(label="Submission completed", state="complete")
                        render_workflow(
                            workflow_placeholder,
                            len(WORKFLOW_STEPS),
                        )
                        pause_for_review(presentation_delay)
                        st.rerun()

        if submission_result is not None and submission_result.success:
            if submission_result.production:
                st.success(
                    f"Wikipedia revision {submission_result.revision_id} was saved."
                )
                if submission_result.revision_url is not None:
                    st.link_button(
                        "View saved revision",
                        submission_result.revision_url,
                    )
            else:
                st.success("Simulation completed. No Wikipedia edit was saved.")
