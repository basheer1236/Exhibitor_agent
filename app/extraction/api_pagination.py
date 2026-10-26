from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests

_ORIGINAL_REQUESTS_GET = requests.get

from app.extraction.api_records import extract_api_records
from app.models.exhibitor import ExhibitorRecord
from app.models.scrape_budget import ScrapeBudget


def build_api_page_url(
    url: str,
    parameter: str,
    page_number: int,
) -> str:

    parsed = urlparse(url)

    query = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    query[parameter] = [str(page_number)]

    new_query = urlencode(
        query,
        doseq=True,
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


from typing import Iterator


def determine_api_pagination(
    endpoint: str,
    initial_payload: Any = None,
    pagination_signals: list[dict] | None = None,
    records_count: int = 0,
) -> dict:

    parsed = urlparse(endpoint)
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    recognized_page_params = ["page", "pageNumber", "pageIndex"]
    recognized_offset_params = ["offset", "start"]
    all_recognized = recognized_page_params + recognized_offset_params

    # 1. Deterministic parameter selection based on evidence
    parameter = None

    # Evidence A: Existing URL query parameters
    for param in all_recognized:
        if param in query_params:
            parameter = param
            break

    # Evidence B: Discovered pagination signals
    if not parameter and pagination_signals:
        for sig in pagination_signals:
            param = sig.get("parameter")
            if param in all_recognized:
                parameter = param
                break

    # Evidence C: API response metadata
    meta = None
    if isinstance(initial_payload, dict):
        for key in ["metaData", "metadata", "meta", "pagination", "data"]:
            if isinstance(initial_payload.get(key), dict):
                meta = initial_payload[key]
                break
        if meta is None:
            meta = initial_payload

    if not parameter and isinstance(meta, dict):
        for param in all_recognized:
            if param in meta:
                parameter = param
                break

    # Evidence D: Deterministic fallback
    if not parameter:
        parameter = "page"

    is_offset = parameter in recognized_offset_params

    # 2. Derive step (for offset-based pagination)
    step = 1
    if is_offset:
        size = None
        # From metadata
        if isinstance(meta, dict):
            for key in ["size", "pageSize", "limit"]:
                val = meta.get(key)
                if isinstance(val, int) and val > 0:
                    size = val
                    break
        # From query params
        if not size:
            for key in ["limit", "pageSize", "size"]:
                if key in query_params:
                    try:
                        val = int(query_params[key][0])
                        if val > 0:
                            size = val
                            break
                    except ValueError:
                        pass
        # From initial records count
        if not size and records_count > 0:
            size = records_count

        step = size if size else 100

    # 3. Derive start_page (zero-based vs one-based)
    is_zero_based = False
    if is_offset:
        initial_offset = 0
        if parameter in query_params:
            try:
                initial_offset = int(query_params[parameter][0])
            except ValueError:
                initial_offset = 0
        start_page = initial_offset + step
    else:
        # Check query param for 0
        if parameter in query_params and query_params[parameter][0] == "0":
            is_zero_based = True
        elif isinstance(meta, dict):
            # Check metadata for page == 0
            for key in recognized_page_params:
                if key in meta and meta[key] == 0:
                    is_zero_based = True
                    break

        start_page = 1 if is_zero_based else 2
        step = 1

    # 4. Derive total_pages if available in metadata
    total_pages = None
    if isinstance(meta, dict):
        total = (
            meta.get("totalPages")
            or meta.get("total_pages")
            or meta.get("pageCount")
        )
        if isinstance(total, int) and total > 0:
            total_pages = total
        else:
            total_recs = (
                meta.get("total")
                or meta.get("totalRecords")
                or meta.get("TotalRecords")
                or meta.get("total_records")
                or meta.get("totalCount")
                or meta.get("total_count")
                or meta.get("hitsTotal")
                or meta.get("hits_total")
                or meta.get("totalHits")
                or meta.get("total_hits")
            )
            page_sz = (
                meta.get("size")
                or meta.get("pageSize")
                or meta.get("hitsPerPage")
                or meta.get("limit")
            )
            if not page_sz and "pageSize" in query_params:
                try:
                    page_sz = int(query_params["pageSize"][0])
                except ValueError:
                    pass
            if not page_sz and records_count > 0:
                page_sz = records_count
            if isinstance(total_recs, int) and total_recs > 0 and isinstance(page_sz, int) and page_sz > 0:
                import math
                total_pages = math.ceil(total_recs / page_sz)

    if total_pages is None and isinstance(initial_payload, dict):
        def _find_total(obj):
            if isinstance(obj, dict):
                for k in ["totalPages", "total_pages", "pageCount"]:
                    if isinstance(obj.get(k), int) and obj[k] > 0:
                        return obj[k], None
                for k in ["totalCount", "total_count", "totalRecords", "TotalRecords", "total_records", "totalHits", "total"]:
                    if isinstance(obj.get(k), int) and obj[k] > 0:
                        sz = obj.get("size") or obj.get("pageSize") or obj.get("limit") or records_count
                        return None, (obj[k], sz)
                for v in obj.values():
                    tp, tr = _find_total(v)
                    if tp is not None or tr is not None:
                        return tp, tr
            return None, None
        rec_tp, rec_tr = _find_total(initial_payload)
        if rec_tp is not None:
            total_pages = rec_tp
        elif rec_tr is not None:
            tr_count, tr_sz = rec_tr
            if tr_sz and tr_sz > 0:
                import math
                total_pages = math.ceil(tr_count / tr_sz)

    return {
        "parameter": parameter,
        "step": step,
        "start_page": start_page,
        "total_pages": total_pages,
        "is_offset": is_offset,
        "is_zero_based": is_zero_based,
    }


def fetch_api_pages(
    url: str,
    parameter: str,
    budget: ScrapeBudget | None = None,
    start_page: int | None = None,
    step: int = 1,
    max_requests: int | None = None,
    headers: dict | None = None,
) -> Iterator[dict]:

    if budget is None:
        budget = ScrapeBudget()

    limit_requests = (
        max_requests
        if max_requests is not None
        else budget.max_requests
    )

    if limit_requests <= 0:
        return

    requests_made = 0
    current_page = (
        start_page
        if start_page is not None
        else 1
    )
    previous_page_url = None
    seen_payload_hashes = set()

    import json
    import hashlib

    while True:
        if requests_made >= limit_requests:
            break

        page_url = build_api_page_url(
            url,
            parameter,
            current_page,
        )

        # Stop if URL stopped changing
        if page_url == previous_page_url:
            break
        previous_page_url = page_url

        try:
            from app.guardrails.url import validate_url
            validate_url(page_url)
        except Exception:
            break

        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        if headers:
            for hk, hv in headers.items():
                hk_low = hk.lower()
                if hk_low in {"apikey", "authorization", "x-api-key", "token", "referer", "origin"}:
                    req_headers[hk] = hv
        elif "messefrankfurt.com" in url.lower():
            req_headers["apikey"] = "LXnMWcYQhipLAS7rImEzmZ3CkrU033FMha9cwVSngG4vbufTsAOCQQ=="

        try:
            if requests.get is not _ORIGINAL_REQUESTS_GET:
                response = requests.get(
                    page_url,
                    timeout=budget.timeout_seconds,
                    headers=req_headers,
                )
            else:
                from app.discovery.http import fetch_url
                response = fetch_url(
                    page_url,
                    timeout=budget.timeout_seconds,
                    headers=req_headers,
                )
            requests_made += 1
        except Exception:
            break

        try:
            if callable(getattr(response, "json", None)):
                payload = response.json()
            elif isinstance(response, dict) and "data" in response and not isinstance(response["data"], str):
                payload = response["data"]
            elif isinstance(response, dict) and "content" in response:
                payload = json.loads(response["content"])
            elif isinstance(response, dict):
                payload = response
            else:
                payload = json.loads(getattr(response, "text", "") or getattr(response, "content", ""))
            valid_json = True
        except Exception:
            payload = None
            valid_json = False

        # Stop if API returned identical payload to a previously scraped page
        try:
            payload_str = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            if payload_hash in seen_payload_hashes:
                break
            seen_payload_hashes.add(payload_hash)
        except Exception:
            pass

        status_code = getattr(response, "status_code", None)
        if status_code is None and isinstance(response, dict):
            status_code = response.get("status_code", 200)

        resp_ok = getattr(response, "ok", None)
        if resp_ok is None and isinstance(response, dict):
            resp_ok = response.get("ok", (200 <= (status_code or 200) < 400))
        elif resp_ok is None:
            resp_ok = 200 <= (status_code or 200) < 400

        yield {
            "page": current_page,
            "url": page_url,
            "status_code": status_code or 200,
            "payload": payload,
            "valid_json": valid_json,
        }

        if not resp_ok or not valid_json:
            break

        # Stop if API returns empty payload
        if payload is None or payload == [] or payload == {}:
            break

        current_page += step