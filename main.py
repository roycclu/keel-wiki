from pprint import pprint

from steps import (
    find_citation_needed, 
    parser_extract_citation_targets, 
    search_web, 
    judge_support_from_sources, 
    submit_with_citation,
    prepare_citation_edit
)
from models import (
    PreparedCitationEdit,
    TargetPage,
    CitationTarget,
    WebSearchEvidence,
    DecisionCitationSupport,
    CitationSubmissionResult
)

from pywikibot import config
config.simulate = False


def main():
    page_targets: list[TargetPage] = find_citation_needed()
    if not page_targets:
        raise ValueError("No target pages found")
    target_page = page_targets[0]
    print("Next page target")
    pprint(target_page.title)
    pprint(target_page.url)
    pprint(target_page.base_revid)

    citation_targets: list[CitationTarget] = parser_extract_citation_targets(target_page, 100, 10)
    if not citation_targets:
        raise ValueError("No target citations found")
    for target_citation in citation_targets:
        print("Next citation target")
        pprint(target_citation.original_template)
        pprint(target_citation.marker)
        pprint(target_citation.context)

    target_citation = citation_targets[0]
    web_hits: list[WebSearchEvidence] = search_web(target_citation)
    if not web_hits:
        raise ValueError("No web hits found")
    for hit in web_hits:
        print("Next web hit")
        pprint({
            "title": hit.title,
            "url": hit.url,
            "description": hit.description,
            "extra_snippets": hit.extra_snippets[0]
        })

    decision_support: DecisionCitationSupport = judge_support_from_sources(citation_target = target_citation, web_hits=web_hits)
    print("Decision on support:")
    pprint(decision_support.model_dump(mode="json"))
    if not decision_support.supports_claim:
        return
    evidence_index = decision_support.evidence_index
    if evidence_index is None:
        raise ValueError("No supporting evidence was selected")
    if not 0 <= evidence_index < len(web_hits):
        raise ValueError("Selected evidence index is out of range")

    prepared_citation: PreparedCitationEdit = prepare_citation_edit(target_page, target_citation, web_hits[decision_support.evidence_index])

    citation_submission_result: CitationSubmissionResult = submit_with_citation(target_page, prepared_citation)

if __name__ == "__main__":
    main()