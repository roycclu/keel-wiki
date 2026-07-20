import json
import pywikibot
from pywikibot import config
import requests

from models import (
    TargetPage,
    CitationTargetSelection,
    CitationTarget,
    WebSearchEvidence,
    DecisionCitationSupport,
    PreparedCitationEdit,
    CitationSubmissionResult
)

WIKI_SITE: str = "wikipedia:en"

from random import choice
from string import ascii_uppercase

def find_citation_needed(
    first_letter: str | None = None, 
    limit: int = 15
    ) -> list[TargetPage]:

    site = pywikibot.Site(WIKI_SITE)

    # template = pywikibot.Page(site, "Template:Citation needed")
    # pages = template.getReferences(
    #     only_template_inclusion=True,
    #     namespaces=[0],
    #     total=limit,    
    #     content=True
    # )
    if first_letter is None:
        first_letter = choice(ascii_uppercase) 
        
    pages = site.search(
        f'hastemplate:"Citation needed" prefix:{first_letter}',
        namespaces=[0],
        total=limit, 
        content=True,
        sort="random"
    )

    target_pages = [
        TargetPage.model_validate({
            "title": page.title(),
            "url": page.full_url(),
            "wikitext": page.text,
            "base_revid": page.latest_revision_id
        })
        for page in pages
    ]

    return target_pages




import mwparserfromhell

CITATION_TEMPLATE_NAMES = {
    "citation needed",
    "cn"
}

def parser_extract_citation_targets(
        target_page: TargetPage, 
        context_before_len: int = 200, 
        context_after_len: int = 10
    ) -> list[CitationTarget]:

    wiki_text = target_page.wikitext
    wikicode = mwparserfromhell.parse(wiki_text)
    original_wiki_text=str(wikicode)

    citation_targets = []

    for index, template in enumerate(wikicode.filter_templates(recursive=True)):
        template_name = str(template.name).strip().casefold()

        if template_name not in CITATION_TEMPLATE_NAMES:
            continue

        marker = f"<<<KEEL_CITATION_NEEDED_{index}>>>"

        original_template = str(template)
        wikicode.replace(template, marker)
        marked_wikitext = str(wikicode)

        marker_position = marked_wikitext.index(marker)
        context_start_position = max(0, marker_position - context_before_len)
        context_end_position = min(len(marked_wikitext), marker_position + len(marker) + context_after_len)
        context=marked_wikitext[context_start_position:marker_position]+marked_wikitext[marker_position+len(marker):context_end_position]

        citation_targets.append(
            CitationTarget(
                title = target_page.title,
                marker = marker,
                original_template=original_template,
                context=context,
                marker_position=marker_position,
                marked_wikitext=marked_wikitext
            )
        )

    return citation_targets






DECISION_PICK_TARGET_CITATION_FROM_MANY = {
    "role": "system",
    "content": "Determine which of the missing citations would be most likely to find web sources that support its claim. "
                "Pick one one citation and return its index as ordered in the . "
                "Always pick one. "
                "Do not invent facts or metadata. "
}

def pick_one_probable_citation_target(citation_targets: list[CitationTarget]) -> CitationTarget:

    client = OpenAI()

    response = client.responses.parse(
        model="gpt-5.6",
        input=[
            DECISION_PICK_TARGET_CITATION_FROM_MANY,
            {
                "role": "user",
                "content": json.dumps(
                    [
                        citation_target.model_dump(mode="json")
                        for citation_target in citation_targets
                    ]
                )
            },
        ],
        text_format=CitationTargetSelection
    )

    citation_target_selection = response.output_parsed
    selected_index = citation_target_selection.selected_index
    if not 0 <= selected_index < len(citation_targets):
        raise ValueError("Selected citation target index is out of range")
    return citation_targets[selected_index]




import os
from dotenv import load_dotenv
load_dotenv()
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

def search_web_for_citation_support(
    citation_target: CitationTarget,
    limit: int = 10
) -> list[WebSearchEvidence]:

    excluded_domains = (
        "NOT site:wikipedia.org "
        "NOT site:reddit.com "
        "NOT site:quizlet.com "
        "NOT site:quora.com "
    )

    query = f"{citation_target.context} {excluded_domains}"

    response = requests.get(
        BRAVE_SEARCH_URL,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]
        },
        params={
            "q": query,
            "count": limit, 
            "country": "US",
            "search_lang": "en",
            "safesearch": "moderate",
            "extra_snippets": "true"
        },
        timeout=30
    )

    response.raise_for_status()
    raw_results = response.json().get("web", {}).get("results", [])

    web_hits = [
        WebSearchEvidence.model_validate(
            {
                "title": result["title"],
                "description": result.get("description", ""),
                "extra_snippets": result.get("extra_snippets", []),
                "url": result["url"],
            }
        )
        for result in raw_results if result.get("extra_snippets")
    ]
    return web_hits





from datetime import date
from openai import OpenAI

DECISION_JUDGE_SUPPORT_FROM_SOURCES = {
    "role": "system",
    "content": "Determine whether any of the sources directly supports the claim before the citation-needed marker. "
                "Pick one supporting source and return the output based on output schema. "
                "If none of the sources support, output does not support false boolean. "
                "Do not invent facts or metadata. "
}

def judge_support_from_sources_by_llm(
    *,
    citation_target: CitationTarget, 
    web_hits: list[WebSearchEvidence]
    ) -> DecisionCitationSupport:

    payload = {
        "claim_context": citation_target.context,
        "web_hits": [
            {   
                "evidence_index": index,
                **hit.model_dump(mode="json"),
            }
            for index, hit in enumerate(web_hits)
        ],
    }

    client = OpenAI()
    response = client.responses.parse(
        model="gpt-5.6",
        input=[
            DECISION_JUDGE_SUPPORT_FROM_SOURCES,
            {
                "role": "user",
                "content": json.dumps(payload),
            }
        ],
        text_format=DecisionCitationSupport
    )

    decision = response.output_parsed
    if decision is None:
        raise RuntimeError("OpenAI returned no parsed support decision")
    return decision





def prepare_citation_edit(
    target_page: TargetPage,
    citation_target: CitationTarget,
    web_hit: WebSearchEvidence
) -> PreparedCitationEdit:

    citation = (
        "<ref>{{cite web "
        f"|title={web_hit.title} "
        f"|url={web_hit.url} "
        f"|access-date={date.today().isoformat()}"
        "}}</ref>"
    )   
    new_wikitext = citation_target.marked_wikitext.replace(citation_target.marker, citation, 1)

    return PreparedCitationEdit(
        original_wikitext=target_page.wikitext,
        new_wikitext=new_wikitext,
        citation=citation,
    )







def submit_with_citation(
    target_page: TargetPage,
    citation_edit: PreparedCitationEdit
    ) -> CitationSubmissionResult:

    site = pywikibot.Site(WIKI_SITE)
    page = pywikibot.Page(site, target_page.title)

    if page.latest_revision_id != target_page.base_revid:
        raise RuntimeError(
            "Wikipedia page changed after it was retrieved"
        )

    current_wikitext = page.text
    pywikibot.showDiff(current_wikitext, citation_edit.new_wikitext)
    # confirmation = input("Save this edit? [y/N] ")
    # if confirmation.strip().casefold() != "y":
    #     print("Edit cancelled")
    #     return
    
    page.text = citation_edit.new_wikitext
    page.save(
        summary="Add citation for a previously unsupported claim",
        minor=False,
        bot=False,
        nocreate=True
    )

    if config.simulate:
        return CitationSubmissionResult(
            production=False,
            success=True,
        )

    revision_id = page.latest_revision_id
    return CitationSubmissionResult(
        production=True,
        success=True,
        revision_id=revision_id,
        revision_url=page.permalink(
            oldid=revision_id,
            with_protocol=True
        )
    )
    