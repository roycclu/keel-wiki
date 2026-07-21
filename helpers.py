from time import monotonic
from urllib.parse import urlsplit
from collections import deque
import requests
from requests import Response
from pywikibot.comms import http
from pywikibot import config

config.simulate = True


request_times: deque[float] = deque()


def observe_http_response(
    response: Response,
    *args: object,
    **kwargs: object,
) -> None:
    WINDOW_SECONDS = 60
    now = monotonic()
    cutoff = now - WINDOW_SECONDS
    while request_times and request_times[0] < cutoff:
        request_times.popleft()

    request_times.append(now)

    request_url = response.request.url or ""
    parsed_url = urlsplit(request_url)
    # parameters = parse_qs(parsed_url.query)
    # action = parameters.get("action", ["unknown"])[0]

    # retry_after = response.headers.get("Retry-After", "-")
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
