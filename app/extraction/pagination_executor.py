from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests

_ORIGINAL_REQUESTS_GET = requests.get

from app.models.scrape_budget import ScrapeBudget


def build_page_url(
    url: str,
    parameter: str,
    value: int,
    path_pattern: str | None = None,
) -> str:
    parsed = urlparse(url)

    effective_pattern = path_pattern
    if not effective_pattern and parameter.startswith("path:"):
        effective_pattern = parameter[5:]

    if effective_pattern:
        new_path = effective_pattern.replace("{page}", str(value))
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                new_path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    query = parse_qs(
        parsed.query,
        keep_blank_values=True
    )

    query[parameter] = [str(value)]

    new_query = urlencode(
        query,
        doseq=True
    )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


import hashlib
from typing import Iterator

def fetch_pages(
    url: str,
    parameter: str,
    start_page: int = 1,
    budget: ScrapeBudget | None = None,
    path_pattern: str | None = None,
    total_pages: int | None = None,
    step: int = 1,
) -> Iterator[dict]:

    if budget is None:
        budget = ScrapeBudget()

    requests_made = 0
    seen_content_hashes: set[str] = set()
    previous_page_url = None

    for page_number in range(
        start_page,
        start_page + budget.max_pages
    ):
        if total_pages is not None and page_number > total_pages:
            break

        # Prevent request budget from being exceeded
        if requests_made >= budget.max_requests:
            break

        if step > 1:
            param_val = (page_number - 1) * step
        elif parameter == "offset":
            param_val = page_number - 1
        else:
            param_val = page_number

        page_url = build_page_url(
            url,
            parameter,
            param_val,
            path_pattern=path_pattern,
        )

        # Stop if pagination URL stopped changing
        if page_url == previous_page_url:
            break
        previous_page_url = page_url

        try:
            from app.guardrails.url import validate_url
            validate_url(page_url)
        except Exception:
            break

        try:
            if requests.get is not _ORIGINAL_REQUESTS_GET:
                response = requests.get(
                    page_url,
                    timeout=budget.timeout_seconds,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                )
            else:
                from app.discovery.http import fetch_url
                response = fetch_url(
                    page_url,
                    timeout=budget.timeout_seconds,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                )
        except Exception:
            break

        requests_made += 1

        if not response.ok:
            break

        content = response.text.strip()

        # Stop when server returns an empty response
        if not content:
            break

        # Stop when server returns identical content (pagination parameter ignored/exhausted)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in seen_content_hashes:
            break

        seen_content_hashes.add(content_hash)

        yield {
            "page": page_number,
            "url": page_url,
            "status_code": response.status_code,
            "content": content,
        }