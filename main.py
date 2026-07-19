import pywikibot
from pprint import pprint
import json

from collections import deque
from time import monotonic
from urllib.parse import parse_qs, urlsplit
import requests
from requests import Response
from pywikibot.comms import http
import mwparserfromhell

from typing import Any
from dataclasses import dataclass

import os

WINDOW_SECONDS = 60
WARNING_THRESHOLD = 8

request_times: deque[float] = deque()

def observe_http_response(
    response: Response,*args: object, **kwargs: object,
) -> None:
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


wiki_api_base_url: str = "https://test.wikipedia.org/w/api.php"


def find_citation_needed(limit: int = 10) -> list[dict[str, Any]]:
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


    site = pywikibot.Site('wikipedia:en')
    template = pywikibot.Page(site, "Template:Citation needed")

    # pprint(template)

    pages = template.getReferences(
        only_template_inclusion=True,
        namespaces=[0],
        total=20,    
        content=True
    )

    pages_cn = []

    for page in pages:
        # print(page.title())
        # print(page.text)
        pages_cn.append({"title": page.title(), "wikitext": page.text} )

    return pages_cn
        # for snippet in parser.extract(page.text)



CITATION_TEMPLATE_NAMES = {
    "citation needed",
    "cn"
}

@dataclass(frozen=True)
class CitationTarget:
    """A citation market and the local context needed to replace it."""
    marker: str
    original_template: str
    context: str
    marked_wikitext: str



def parser_extract(
        wiki_text, 
        context_before_len: int = 200, 
        context_after_len: int = 200
    ) -> list[CitationTarget]:

    wikicode = mwparserfromhell.parse(wiki_text)

    targets = []

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

        target = CitationTarget(
            marker = marker,
            original_template=original_template,
            context=marked_wikitext[context_start_position:context_end_position],
            marked_wikitext=marked_wikitext
        )

        targets.append(target)

    return targets





BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

def search_web(
    query: str,
    count: int = 10
):
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
            "safesearch": "moderate"
        },
        timeout=30
    )

    response.raise_for_status()
    print(f"web search result: {response.json()}")


import httpx

async def submit_with_citation():
    async with httpx.AsyncClient() as client:

        payload = {
        "action": "edit",
        #   "title": p.title,
        #   "text": p.new_wikitext,
        #   "summary": p.summary,
        #   "baserevid": p.base_revid,
        "nocreate": 1,
        #   "token": csrf_token,
        }

        response = await client.post(
            wiki_api_base_url,
            data = payload,
            headers={
                "Authorization": f"Bearer",
                "User-Agent": "Keel/0.1"
            },
        )
        response.raise_for_status()
        print(f"submission response: {response.json()}")


def main():
    pages_cn = find_citation_needed()

    if not pages_cn:
        print("No pages found")

    targets_cn = parser_extract(pages_cn[0]["wikitext"], 100, 100)

    for tcn in targets_cn:
        pprint(tcn.original_template)
        pprint(tcn.context)

    web_hit = search_web(targets_cn[0].context)


    # repo = site.data_repository()
    # page = repo.page_from_repository('Q91')
    # item = pywikibot.ItemPage(repo, 'Q91')
    # data = item.get()
    # data_json = json.dumps(item.toJSON(), indent=2, ensure_ascii=False,)

    # tree = [page, item, data, data_json]


    # for name, value in locals().items():
    #     # pprint(f"{name}: {value}")
    #     print(f"{name}:")
    #     pprint(value)

if __name__ == "__main__":
    main()