from random import choice
from datetime import datetime, timezone
from pprint import pprint
import uuid
from pathlib import Path

from steps import (
    find_citation_needed,
    parser_extract_citation_targets,
    pick_one_probable_citation_target,
    search_web_for_citation_support,
    judge_support_from_sources_by_llm,
    submit_with_citation,
    prepare_citation_edit,
)

from models import (
    PreparedCitationEdit,
    TargetPage,
    CitationTarget,
    WebSearchEvidence,
    DecisionCitationSupport,
    CitationSubmissionResult,
    WorkflowRunResult
)

RESULTS_FILE = Path(__file__).parent / "workflow_runs.jsonl"

def append_result(result: WorkflowRunResult) -> None:
    with RESULTS_FILE.open("a", encoding="utf-8") as file:
        file.write(result.model_dump_json() + "\n")

def start_task():
    stage = "starting"
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)
    try: 
        stage = "fetching_page_targets"
        page_targets: list[TargetPage] = find_citation_needed(limit=5)
        if not page_targets:
            raise ValueError("No target pages found")
        for tp in page_targets:
            print("Next page target")
            pprint(tp.title)
            pprint(tp.url)

        target_page = choice(page_targets)

        stage ="parsing_citation_targets"
        citation_targets: list[CitationTarget] = parser_extract_citation_targets(
            target_page, context_before_len=300, context_after_len=10
        )
        if not citation_targets:
            raise ValueError("No target citations found")
        for target_citation in citation_targets:
            print("Next citation target")
            pprint(target_citation.original_template)
            pprint(target_citation.marker)
            pprint(target_citation.context)

        # target_citation = choice(citation_targets)
        stage = "picking_a_citation_target"
        target_citation = pick_one_probable_citation_target(citation_targets)
        print("Chosen citation target")
        pprint(target_citation.original_template)
        pprint(target_citation.marker)
        pprint(target_citation.context)

        stage = "searching_web_for_evidence"
        web_hits: list[WebSearchEvidence] = search_web_for_citation_support(
            target_citation, limit=10
        )
        if not web_hits:
            raise ValueError("No web hits found")
        for hit in web_hits:
            print("Next web hit")
            pprint(
                {
                    "title": hit.title,
                    "url": hit.url,
                    "description": hit.description,
                    "extra_snippets": hit.extra_snippets[0],
                }
            )

        stage = "generating_citation_support_decision"
        decision_support: DecisionCitationSupport = judge_support_from_sources_by_llm(
            citation_target=target_citation, web_hits=web_hits
        )
        print("Decision on support:")
        pprint(decision_support.model_dump(mode="json"))
        if not decision_support.supports_claim:
            raise ValueError("Decision was evidence does not support claim")
        evidence_index = decision_support.evidence_index
        if evidence_index is None:
            raise ValueError("No supporting evidence was selected")
        if not 0 <= evidence_index < len(web_hits):
            raise ValueError("Selected evidence index is out of range")

        stage = "preparing_citation"
        prepared_citation: PreparedCitationEdit = prepare_citation_edit(
            target_page, target_citation, web_hits[evidence_index]
        )

        stage = "submitting_citation"
        citation_submission_result: CitationSubmissionResult = submit_with_citation(
            target_page, prepared_citation
        )

        stage = "wrapping up"
        finished_at = datetime.now(timezone.utc)

    except Exception as e:
        return WorkflowRunResult(
            run_id=run_id,
            started_at=started_at,
            finished_at=None,
            success=False,
            status="FAILURE",
            failed_stage=stage,
            error_type=type(e).__name__,
            error_message=str(e),
            citation_submission_result=None
        )

    return WorkflowRunResult(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        success=True,
        status="SUCCESS",
        failed_stage=None,
        error_type=None,
        error_message=None,
        citation_submission_result=citation_submission_result
    )

def main() -> int:
    result: WorkflowRunResult = start_task()
    append_result(result)
    pprint(result.model_dump(mode="json"))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
