import re
from typing import Any
from urllib.parse import urljoin
import requests
from app.discovery.http import fetch_url


API_PATTERNS = [
    r"/api/",
    r"/api$",
    r"/graphql",
    r"/graphql/",
    r"/v\d+/",
    r"\.json",
    r"\.ashx",
    r"\.axd",
    r"/handlers?/",
    r"handler(?:s)?(?:\.ashx|\.php|\.action|\.do|\?|$)",
    r"(?:exhibitor|company|vendor|directory|participant)listhandler",
    r"/wp-json/",
    r"/(?:custom)?api/",
    r"exhi?bitor(?:s)?_?list",
]

UNRELATED_KEYWORDS = {
    "analytics", "tracking", "telemetry", "metric", "metrics",
    "config", "configuration", "settings", "options",
    "debug", "flags", "featureflags", "tokens", "auth", "session"
}

EXHIBITOR_KEYS = {
    "company", "company_name", "companyname", "name", "title",
    "exhibitor", "exhibitor_name", "vendor", "partner", "member",
    "organization", "firm", "booth", "stand", "hall", "catalog",
    "customer", "client", "account", "interestedcustomer",
}


def find_api_candidates(
    html: str,
    base_url: str
) -> list[str]:

    candidates = set()

    # --------------------------------------------------
    # 1. Find URLs directly present in HTML
    # --------------------------------------------------

    absolute_urls = re.findall(
        r'https?://[^\s"\'<>]+',
        html
    )

    for url in absolute_urls:

        if looks_like_api(url):
            candidates.add(url)

    # --------------------------------------------------
    # 2. Find relative API paths
    # --------------------------------------------------

    relative_paths = re.findall(
        r'["\']([^"\']+)["\']',
        html
    )

    IGNORED_EXTENSIONS_OR_FILES = {
        "manifest.json", "manifest.webmanifest", "site.webmanifest",
        "asset-manifest.json", "schema.json",
    }

    for path in relative_paths:
        clean_path = path.strip(" \\\"'")
        if clean_path.startswith(("Core_", "Query_", "Mutation_")) or (":" in clean_path and not clean_path.startswith(("http://", "https://"))):
            continue
        if looks_like_api(clean_path):
            full_url = urljoin(
                base_url,
                clean_path
            )
            candidates.add(full_url)

    # --------------------------------------------------
    # 3. Find endpoints from AJAX / Fetch calls in scripts
    # --------------------------------------------------
    ajax_calls = re.findall(
        r'(?:\$\.(?:getJSON|get|post|ajax)|fetch|axios(?:\.get)?)\s*\(\s*([^,\);]+)',
        html
    )
    for call_arg in ajax_calls:
        str_parts = re.findall(r'["\']([^"\']+)["\']', call_arg)
        if not str_parts:
            continue
        # Reconstruct url from tokens if concatenated with variables
        tokens = [t.strip() for t in call_arg.split("+")]
        reconstructed = ""
        for t in tokens:
            if (t.startswith('"') and t.endswith('"')) or (t.startswith("'") and t.endswith("'")):
                reconstructed += t[1:-1]
            elif any(p in t.lower() for p in ["page", "index"]):
                reconstructed += "1"
            elif any(s in t.lower() for s in ["size", "count", "limit"]):
                reconstructed += "10"
            else:
                reconstructed += ""
        clean_rec = reconstructed.strip()
        if clean_rec and (looks_like_api(clean_rec) or any(k in clean_rec.lower() for k in ["exhibitor", "company", "directory", "list", "search"])):
            full_url = urljoin(base_url, clean_rec)
            candidates.add(full_url)

    # --------------------------------------------------
    # 4. Find endpoints from data-config or data-component
    # --------------------------------------------------
    import json
    import html as html_lib
    for config_match in re.finditer(r'data-(?:config|component)\s*=\s*(["\'])(.*?)\1', html, re.DOTALL):
        try:
            raw_val = config_match.group(2)
            decoded_val = html_lib.unescape(raw_val)
            if not decoded_val.strip().startswith("{"):
                continue
            cfg = json.loads(decoded_val)
            if isinstance(cfg, dict):
                event_id = cfg.get("API_EVENT_ID")
                lang = cfg.get("LANGUAGE", "en-GB")
                page_sz = cfg.get("PAGE_SIZE", 30)
                if event_id:
                    mf_endpoint = (
                        f"https://api.messefrankfurt.com/service/esb_api/exhibitor-service/api/2.1/public/exhibitor/search"
                        f"?language={lang}&pageNumber=1&pageSize={page_sz}&findEventVariable={event_id}"
                    )
                    candidates.add(mf_endpoint)
                api_url = cfg.get("API_URL")
                if api_url and looks_like_api(api_url):
                    candidates.add(api_url)
        except Exception:
            pass

    valid_candidates = set()
    for c in candidates:
        if any(bad in c for bad in ["\n", "\r", "\t", " ", "{", "}", "(", ")", ";"]):
            continue
        valid_candidates.add(c)

    return sorted(valid_candidates)


def looks_like_api(url: str) -> bool:

    url_lower = url.lower()
    if any(ign in url_lower for ign in ["manifest.json", "manifest.webmanifest", "site.webmanifest", "asset-manifest.json"]):
        return False

    clean_path = url_lower.split("?")[0].split("#")[0]
    if clean_path.endswith((".js", ".mjs", ".css", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".ico")):
        return False

    # Ignore WordPress core metadata endpoints (posts, pages, media, users, taxonomies, etc.)
    if re.search(r"/wp-json/wp/v2/(?:pages|posts|media|types|statuses|taxonomies|users|comments|themes|plugins)(?:/|\?|$)", url_lower):
        return False

    return any(
        re.search(pattern, url_lower)
        for pattern in API_PATTERNS
    )


def verify_api(
    url: str,
    timeout: int = 10
) -> dict:

    try:
        from app.discovery.http import fetch_url
        req_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if "messefrankfurt.com" in url.lower():
            req_headers["apikey"] = "LXnMWcYQhipLAS7rImEzmZ3CkrU033FMha9cwVSngG4vbufTsAOCQQ=="

        response = fetch_url(
            url,
            timeout=timeout,
            headers=req_headers,
        )

        if hasattr(response, "content_type"):
            content_type = response.content_type.lower()
            final_url = response.final_url
            status_code = response.status_code
            ok = response.ok
            get_json = response.json
            raw_text = getattr(response, "text", "") or getattr(response, "content", "")
        else:
            headers = response.get("headers", {})
            content_type = headers.get("Content-Type", headers.get("content-type", "")).lower()
            final_url = response.get("url", url)
            status_code = response.get("status_code", 200)
            ok = (200 <= status_code < 400)
            raw_text = response.get("content", "")
            import json
            get_json = lambda: (
                response.get("json")() if callable(response.get("json"))
                else json.loads(response.get("content", "{}"))
            )

        if isinstance(raw_text, bytes):
            raw_text = raw_text.decode("utf-8", errors="ignore")
        stripped = str(raw_text).strip()

        is_json = (
            "application/json" in content_type
            or (stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")))
        )

        result = {
            "url": final_url,
            "status_code": status_code,
            "is_json": is_json,
            "content_type": content_type,
            "valid": (
                ok and is_json
            )
        }

        if result["valid"]:
            try:
                if "application/json" in content_type:
                    result["payload"] = get_json()
                else:
                    import json
                    result["payload"] = json.loads(stripped)
            except Exception:
                result["valid"] = False
                result["payload"] = None

        return result

    except Exception as exc:

        return {
            "url": url,
            "status_code": None,
            "is_json": False,
            "content_type": "",
            "valid": False,
            "error": str(exc)
        }


def evaluate_api_payload(payload: Any, url: str) -> dict:
    """
    Evaluate whether a JSON payload represents an exhibitor/directory API.
    Rejects configs, analytics, tracking, empty collections, and non-exhibitor schemas.
    """
    from app.extraction.api_records import find_record_collection, extract_api_records

    if payload is None:
        return {
            "valid_exhibitor_api": False,
            "score": 0.0,
            "reason": "Payload is None",
            "useful_records": 0,
            "records": [],
        }

    # Reject WordPress core post/page content objects
    if isinstance(payload, dict):
        if (
            payload.get("type") in {"page", "post", "attachment", "revision"}
            and (
                isinstance(payload.get("content"), dict)
                or isinstance(payload.get("title"), dict)
            )
        ):
            return {
                "valid_exhibitor_api": False,
                "score": 0.0,
                "reason": "WordPress post/page metadata object is not an exhibitor collection",
                "useful_records": 0,
                "records": [],
            }

    collection = find_record_collection(payload)
    if not collection or not isinstance(collection, list) or len(collection) == 0:
        return {
            "valid_exhibitor_api": False,
            "score": 0.0,
            "reason": "Empty or missing collection of objects",
            "useful_records": 0,
            "records": [],
        }

    dict_items = [item for item in collection if isinstance(item, dict)]
    if not dict_items:
        return {
            "valid_exhibitor_api": False,
            "score": 0.0,
            "reason": "Collection items are not objects",
            "useful_records": 0,
            "records": [],
        }

    # Inspect keys across items
    all_keys = set()
    for item in dict_items[:15]:
        all_keys.update(str(k).lower() for k in item.keys())
        for sub_k in ["customer", "client", "account", "exhibitor", "company"]:
            if isinstance(item.get(sub_k), dict):
                all_keys.update(str(k).lower() for k in item[sub_k].keys())

    has_exhibitor_key = any(
        k in EXHIBITOR_KEYS or any(ek in k for ek in EXHIBITOR_KEYS)
        for k in all_keys
    )
    is_mostly_unrelated = (
        len(all_keys & UNRELATED_KEYWORDS) > 0
        and not has_exhibitor_key
    )

    if is_mostly_unrelated:
        return {
            "valid_exhibitor_api": False,
            "score": 0.0,
            "reason": "Collection contains unrelated analytics or configuration data",
            "useful_records": 0,
            "records": [],
        }

    records = extract_api_records(payload, url)
    from app.extraction.html_records import _is_valid_company_name
    useful_records = [
        r for r in records
        if r.company_name and _is_valid_company_name(r.company_name)
    ]

    if not useful_records:
        return {
            "valid_exhibitor_api": False,
            "score": 0.0,
            "reason": "Extracted records lack valid company names",
            "useful_records": 0,
            "records": [],
        }

    total_count = len(records)
    useful_count = len(useful_records)
    useful_ratio = useful_count / max(1, total_count)

    detail_coverage = sum(1 for r in useful_records if r.detail_url) / useful_count
    website_coverage = sum(1 for r in useful_records if r.website) / useful_count
    booth_coverage = sum(1 for r in useful_records if r.booth or r.hall) / useful_count
    contact_coverage = sum(1 for r in useful_records if r.email or r.phone) / useful_count

    has_pagination_evidence = False
    if isinstance(payload, dict):
        pagination_keys = {
            "total", "total_pages", "totalpages", "total_count", "totalcount",
            "count", "page", "page_number", "next", "has_next", "pagination"
        }
        has_pagination_evidence = any(pk in payload for pk in pagination_keys)

    first_keys = set(dict_items[0].keys())
    consistency_matches = sum(
        1 for item in dict_items[1:10] if set(item.keys()) == first_keys
    )
    schema_consistency = (
        consistency_matches / max(1, len(dict_items[1:10]))
        if len(dict_items) > 1
        else 1.0
    )

    score = (
        min(10, useful_count) * 4.0
        + useful_ratio * 20.0
        + (detail_coverage + website_coverage) * 10.0
        + (booth_coverage + contact_coverage) * 15.0
        + (10.0 if has_pagination_evidence else 0.0)
        + schema_consistency * 10.0
    )

    return {
        "valid_exhibitor_api": True,
        "score": round(score, 2),
        "useful_records": useful_count,
        "records": useful_records,
        "detail_coverage": detail_coverage,
        "website_coverage": website_coverage,
        "has_pagination_evidence": has_pagination_evidence,
        "schema_consistency": schema_consistency,
    }


def rank_api_candidates(candidates_with_payload: list[tuple[str, Any]]) -> list[dict]:
    """
    Deterministically rank API candidates based on usefulness, record count,
    coverage, pagination evidence, and schema consistency.
    """
    evaluated = []
    for url, payload in candidates_with_payload:
        eval_res = evaluate_api_payload(payload, url)
        evaluated.append({
            "url": url,
            "payload": payload,
            **eval_res,
        })

    evaluated.sort(
        key=lambda item: (
            item["valid_exhibitor_api"],
            item["score"],
            item["useful_records"],
        ),
        reverse=True,
    )
    return evaluated