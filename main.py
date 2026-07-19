from pprint import pprint
import json
from pydantic import BaseModel, Field, HttpUrl

import requests
from requests import Response


from typing import Any
from dataclasses import dataclass


import pywikibot
from pywikibot.comms import http
from pywikibot import config
config.simulate = True

from time import monotonic
from urllib.parse import parse_qs, urlsplit
from collections import deque

request_times: deque[float] = deque()
def observe_http_response(
    response: Response,*args: object, **kwargs: object,
) -> None:
    WINDOW_SECONDS = 60
    WARNING_THRESHOLD = 8
    now = monotonic()
    cutoff = now - WINDOW_SECONDS
    while request_times and request_times[0] < cutoff:
        request_times.popleft()

    request_times.append(now)
    
    request_url = response.request.url or ""
    parsed_url = urlsplit(request_url)
    parameters = parse_qs(parsed_url.query)
    action = parameters.get("action", ["unknown"])[0]

    retry_after = response.headers.get("Retry-After", "-")
    elapsed_ms = response.elapsed.total_seconds() * 1_000

    print(
        f"HTTP {response.request.method} "
        f"host={parsed_url.netloc} "
        f"path={parsed_url.path} "
        f"status={response.status_code} "
        f"rolling_60s={len(request_times)} "
        f"elapsed_ms={elapsed_ms:.0f} "
     )

http.session.hooks["response"].append(observe_http_response)
session = requests.Session()
session.hooks["response"].append(observe_http_response)



WIKI_API_BASE_URL: str = "https://test.wikipedia.org/w/api.php"
WIKI_SITE: str = "wikipedia:en"

class TargetPage(BaseModel):
    title: str
    url: str
    wikitext: str
    base_revid: int

def find_citation_needed(limit: int = 15) -> list[dict[str, Any]]:
    # response = requests.get(
    #     wiki_api_base,
    #     params={
    #         "action": "query",
    #         "list": "search",
    #         "srsearch": 'insource:"Citation needed"',
    #         "srnamespace": 0,
    #         "srlimit": limit, 
    #         "format": "json",
    #         "formatversion": 2,
    #     },
    #     headers={
    #         "User-Agent": (
    #             "KeelCitationBot/0.1 "
    #             "(https://www.wikidata.org/wiki/User:YourUsername)"
    #         ),
    #     },
    #     timeout=30,
    # )
    # response.raise_for_status()
    # payload = response.json()

    # return payload["query"]["search"]


    site = pywikibot.Site(WIKI_SITE)
    template = pywikibot.Page(site, "Template:Citation needed")

    # pprint(template)

    pages = template.getReferences(
        only_template_inclusion=True,
        namespaces=[0],
        total=limit,    
        content=True
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

@dataclass(frozen=True)
class CitationTarget:
    """A citation market and the local context needed to replace it."""
    title: str
    original_template: str
    context: str
    marker: str
    marker_position: int
    marked_wikitext: str

def parser_extract_citation_targets(
        target_page: TargetPage, 
        context_before_len: int = 200, 
        context_after_len: int = 200
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


import os
from dotenv import load_dotenv
load_dotenv()
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

class WebSearchEvidence(BaseModel):
    """A candidate source returned by web search"""
    title: str = Field(description="Title of the candidate webpage")
    url: HttpUrl = Field(description="Canonical URL of the webpage")
    description: str = Field(description="Readable description of the page content")
    extra_snippets: list[str] = Field(description="Readable snippets from page text")



def search_web(
    citation_target: CitationTarget,
    count: int = 10
):

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
            "count": count, 
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
                "extra_snippets": result.get("extra_snippets") or [],
                "url": result["url"],
            }
        )
        for result in raw_results
    ]
    return web_hits





from datetime import date
from openai import OpenAI

class DecisionCitationSupport(BaseModel): 
    supports_claim: bool
    evidence_index: int | None
    explanation: str

def render_citation(web_hit: WebSearchEvidence, result: DecisionCitationSupport) -> str:

    return (
        "<ref>{{cite web "
        f"|title={web_hit.title} "
        f"|url={web_hit.url} "
        f"|access-date={date.today().isoformat()}"
        "}}</ref>"
    )

DECISION_JUDGE_SUPPORT_FROM_SOURCES = {
    "role": "system",
    "content": "Determine whether any of the sources directly supports the claim before the citation-needed marker. "
                "Pick one supporting source and return the output based on output schema. "
                "If none of the sources support, output does not support false boolean. "
                "Do not invent facts or metadata. "
}

def judge_support_from_sources(
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
    return decision




import httpx

def submit_with_citation(
    target_page: TargetPage,
    citation_target: CitationTarget, 
    web_hit: WebSearchEvidence,
    decision_support: DecisionCitationSupport
    ):
    # async with httpx.AsyncClient() as client:

        citation = render_citation(web_hit, decision_support)
        new_wikitext = citation_target.marked_wikitext.replace(citation_target.marker, citation, 1)

        payload = {
        "action": "edit",
        "title": target_page.title,
        "text": new_wikitext,
        "summary": decision_support.explanation,
        "baserevid": target_page.base_revid,
        "nocreate": 1,
        #   "token": csrf_token,
        }


        site = pywikibot.Site(WIKI_SITE)
        page = pywikibot.Page(site, target_page.title)

        if page.latest_revision_id != target_page.base_revid:
            raise RuntimeError(
                "Wikipedia page changed after it was retrieved"
            )

        current_wikitext = page.text
        pywikibot.showDiff(current_wikitext, new_wikitext)
        confirmation = input("Save this edit? [y/N] ")
        if confirmation.strip().casefold() != "y":
            print("Edit cancelled")
            return
        
        page.text = new_wikitext
        page.save(
            summary="Add citation for a previously unsupported claim",
            minor=False,
            bot=False,
            nocreate=True
        )

        # response = await client.post(
        #     WIKI_API_BASE_URL,
        #     data = payload,
        #     headers={
        #         "Authorization": f"Bearer",
        #         "User-Agent": "Keel/0.1"
        #     },
        # )
        # response.raise_for_status()


def main():
    page_targets: list[TargetPage] = find_citation_needed()
    if not page_targets:
        raise ValueError("No target pages found")

    target_page = page_targets[0]
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

    submit_with_citation(target_page, target_citation, web_hits[decision_support.evidence_index], decision_support)

if __name__ == "__main__":
    main()