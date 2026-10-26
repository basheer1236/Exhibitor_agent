from typing import Optional
from datetime import datetime, timezone

from app.discovery.site import discover_site
from app.discovery.strategy import choose_strategy, should_use_browser
from app.discovery.http import fetch_url
from app.discovery.api import (
    verify_api,
    evaluate_api_payload,
    rank_api_candidates,
)
from app.discovery.html import analyze_html
from app.discovery.pagination import detect_pagination

from app.extraction.html_records import extract_html_records
from app.extraction.deduplicator import (
    deduplicate_records,
    deduplicate_with_stats,
)
from app.extraction.api_records import extract_api_records, find_record_collection
from app.extraction.api_pagination import (
    fetch_api_pages,
    determine_api_pagination,
)
from app.extraction.pagination_executor import fetch_pages
from app.extraction.detail_enricher import (
    associate_detail_urls,
    enrich_records,
    clean_global_organizer_contacts,
)
from app.quality import evaluate_extraction_quality

from app.models.scrape_result import ScrapeResult
from app.models.scrape_budget import ScrapeBudget
from app.browser import BrowserClient
from app.extraction.normalizer import normalize_company_name, normalize_record
from app.models.exhibitor import ExhibitorRecord
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import re
import json


def _collect_new_records(
    page_records: list,
    existing_records: list,
) -> list:
    """
    Deterministically identify genuinely new records for pagination loops.
    Prevents adding duplicate records and detects when pagination has stalled.
    """
    seen_ids = set()
    seen_names = set()
    seen_combos = set()

    for r in existing_records:
        if r.exhibitor_id:
            seen_ids.add(r.exhibitor_id.strip().lower())
        name_clean = normalize_company_name(r.company_name).lower() if r.company_name else ""
        if name_clean:
            seen_names.add(name_clean)
        clean_durl = (r.detail_url or "").split("?")[0].rstrip("/").lower()
        clean_web = (r.website or "").rstrip("/").lower()
        seen_combos.add((name_clean, clean_durl, clean_web))

    new_records = []
    for r in page_records:
        r_eid = (r.exhibitor_id or "").strip().lower()
        r_name = normalize_company_name(r.company_name).lower() if r.company_name else ""
        r_durl = (r.detail_url or "").split("?")[0].rstrip("/").lower()
        r_web = (r.website or "").rstrip("/").lower()

        if r_eid and r_eid in seen_ids:
            continue
        if r_name and r_name in seen_names:
            continue
        if (r_name, r_durl, r_web) in seen_combos:
            continue

        new_records.append(r)
        if r_eid:
            seen_ids.add(r_eid)
        if r_name:
            seen_names.add(r_name)
        seen_combos.add((r_name, r_durl, r_web))

    return new_records


def _paginate_html(
    profile,
    result: ScrapeResult,
    budget: ScrapeBudget,
) -> bool:
    budget_limited = False
    if profile.has_pagination and profile.pagination_signals:
        signal = profile.pagination_signals[0]
        parameter = signal.get("parameter")
        path_pattern = signal.get("path_pattern")
        total_pages = signal.get("total_pages")
        step = signal.get("step", 1)
        
        if parameter:
            fetch_kwargs = {}
            if path_pattern is not None:
                fetch_kwargs["path_pattern"] = path_pattern
            if total_pages is not None:
                fetch_kwargs["total_pages"] = total_pages
            if step > 1:
                fetch_kwargs["step"] = step
            consecutive_duplicate_pages = 0
            for page_dict in fetch_pages(
                profile.url, 
                parameter, 
                start_page=2, 
                budget=budget,
                **fetch_kwargs,
            ):
                page_records = extract_html_records(
                    page_dict["content"],
                    page_dict["url"],
                )
                
                if not page_records:
                    break

                result.requests_made += 1
                result.pages_scraped += 1

                if result.pages_scraped >= budget.max_pages:
                    budget_limited = True

                # Identify genuinely new records
                new_records = _collect_new_records(page_records, result.records)
                if not new_records and page_records:
                    consecutive_duplicate_pages += 1
                    max_dup_tolerance = 5 if total_pages else 1
                    if consecutive_duplicate_pages >= max_dup_tolerance:
                        break
                else:
                    consecutive_duplicate_pages = 0

                remaining = budget.max_records - len(result.records)
                if remaining <= 0:
                    budget_limited = True
                    break

                result.records.extend(new_records[:remaining])

                if len(result.records) >= budget.max_records:
                    budget_limited = True
                    break
                if result.pages_scraped >= budget.max_pages:
                    budget_limited = True
                    break
    return budget_limited


def _execute_browser_strategy(
    url: str,
    profile,
    result: ScrapeResult,
    budget: ScrapeBudget,
    browser_client: Optional[BrowserClient] = None,
) -> None:
    result.browser_used = True
    if not result.fallback_reason:
        result.fallback_reason = "Headless browser rendering executed for JavaScript-dependent page."

    if browser_client is None:
        browser_client = BrowserClient()

    remaining_requests = max(0, budget.max_requests - result.requests_made)
    if remaining_requests <= 0:
        result.errors.append("Browser request budget exhausted before rendering.")
        return

    if hasattr(budget, "model_copy"):
        browser_budget = budget.model_copy(update={"max_requests": remaining_requests})
    else:
        browser_budget = budget.copy(update={"max_requests": remaining_requests})

    try:
        render_result = browser_client.render(url, budget=browser_budget)
    except Exception as exc:
        result.errors.append(f"Browser rendering failed: {exc}")
        return

    result.requests_made += max(1, render_result.requests_made)
    result.final_url = render_result.final_url
    if hasattr(render_result, "rendered_html") and render_result.rendered_html:
        setattr(result, "_rendered_html", render_result.rendered_html)
    if render_result.errors:
        result.errors.extend(render_result.errors)

    # 1. Check if the browser session intercepted any valid exhibitor API calls
    api_records_extracted = False
    valid_api_candidates = []
    for item in getattr(render_result, "intercepted_apis", []):
        api_url = item[0]
        api_payload = item[1]
        api_headers = item[2] if len(item) > 2 else {}
        eval_result = evaluate_api_payload(api_payload, api_url)
        if eval_result.get("valid_exhibitor_api"):
            valid_api_candidates.append({
                "url": api_url,
                "payload": api_payload,
                "headers": api_headers,
                "score": eval_result.get("score", 0.0),
                "useful_records": eval_result.get("useful_records", 0),
            })

    if valid_api_candidates:
        from urllib.parse import urlparse
        valid_api_candidates.sort(key=lambda c: (c["score"], c["useful_records"]), reverse=True)
        best_candidate = valid_api_candidates[0]
        best_parsed = urlparse(best_candidate["url"])
        best_endpoint_path = best_parsed.path
        best_endpoint_host = best_parsed.netloc.lower()

        # Find all intercepted pages matching the best API's base endpoint
        matching_intercepts = [
            c for c in valid_api_candidates
            if urlparse(c["url"]).netloc.lower() == best_endpoint_host
            and urlparse(c["url"]).path == best_endpoint_path
        ]

        seen_names = {r.company_name.lower() for r in result.records if r.company_name}
        for cand in matching_intercepts:
            api_recs = extract_api_records(cand["payload"], render_result.final_url or cand["url"])
            new_recs = [r for r in api_recs if r.company_name and r.company_name.lower() not in seen_names]
            for r in new_recs:
                seen_names.add(r.company_name.lower())
            rem = budget.max_records - len(result.records)
            if rem <= 0:
                break
            result.records.extend(new_recs[:rem])
            result.pages_scraped += 1

        if result.records:
            result.strategy = "BROWSER_API"
            api_records_extracted = True

            api_url = best_candidate["url"]
            api_payload = best_candidate["payload"]
            api_headers = best_candidate.get("headers", {})
            pagination_info = determine_api_pagination(
                api_url,
                initial_payload=api_payload,
                records_count=len(result.records),
            )
            step = pagination_info.get("step", 1)
            total_p = pagination_info.get("total_pages")
            start_p = result.pages_scraped + 1

            if len(result.records) < budget.max_records and (not total_p or result.pages_scraped < total_p):
                for page_payload in fetch_api_pages(
                    api_url,
                    parameter=pagination_info["parameter"],
                    budget=budget,
                    start_page=start_p,
                    step=step,
                    headers=api_headers,
                ):
                    if len(result.records) >= budget.max_records:
                        break
                    if total_p and result.pages_scraped >= total_p:
                        break
                    more_recs = extract_api_records(page_payload, render_result.final_url or api_url)
                    if not more_recs:
                        break
                    new_more = [r for r in more_recs if r.company_name and r.company_name.lower() not in seen_names]
                    if not new_more and more_recs:
                        break
                    for r in new_more:
                        seen_names.add(r.company_name.lower())
                    rem = budget.max_records - len(result.records)
                    result.records.extend(new_more[:rem])
                    result.pages_scraped += 1
                    result.requests_made += 1

    # 2. If no valid exhibitor API was intercepted, extract from hydrated HTML
    if not api_records_extracted:
        rendered_html = render_result.rendered_html
        records = extract_html_records(rendered_html, render_result.final_url)

        if records:
            remaining = budget.max_records - len(result.records)
            result.records.extend(records[:remaining])
            result.pages_scraped += 1
            result.strategy = "BROWSER_HTML"

        # Check for iframes inside rendered DOM (bounded depth = 1)
        if len(result.records) < budget.max_records:
            rendered_html_info = analyze_html(rendered_html, render_result.final_url)
            rendered_iframes = rendered_html_info.get("iframe_urls", [])
            if rendered_iframes:
                for iframe_url in rendered_iframes:
                    if result.requests_made >= budget.max_requests:
                        break
                    if len(result.records) >= budget.max_records:
                        break
                    try:
                        iframe_resp = fetch_url(iframe_url, timeout=budget.timeout_seconds)
                        result.requests_made += 1
                        iframe_content = (
                            iframe_resp.content
                            if hasattr(iframe_resp, "content")
                            else iframe_resp.get("content", "")
                        )
                        iframe_final_url = (
                            iframe_resp.final_url
                            if hasattr(iframe_resp, "final_url")
                            else iframe_resp.get("url", iframe_url)
                        )
                        iframe_records = extract_html_records(iframe_content, iframe_final_url)
                        if iframe_records:
                            rem = budget.max_records - len(result.records)
                            result.records.extend(iframe_records[:rem])
                            result.pages_scraped += 1
                            result.strategy = "BROWSER_HTML"
                    except Exception as exc:
                        result.errors.append(f"Failed to fetch rendered iframe {iframe_url}: {exc}")

        # Deterministic rendered pagination
        if len(result.records) < budget.max_records and result.pages_scraped < budget.max_pages:
            rendered_html_info = analyze_html(rendered_html, render_result.final_url)
            pagination_links = [
                link["url"] for link in rendered_html_info.get("links", [])
            ]
            pagination_info = detect_pagination(
                render_result.final_url,
                pagination_links,
            )

            if (
                pagination_info["has_pagination"]
                and pagination_info["signals"]
                and len(result.records) < budget.max_records
            ):
                signal = pagination_info["signals"][0]
                parameter = signal.get("parameter")
                path_pattern = signal.get("path_pattern")
                total_pages = signal.get("total_pages")
                step = signal.get("step", 1)
                if parameter:
                    fetch_kwargs = {}
                    if path_pattern is not None:
                        fetch_kwargs["path_pattern"] = path_pattern
                    if total_pages is not None:
                        fetch_kwargs["total_pages"] = total_pages
                    if step > 1:
                        fetch_kwargs["step"] = step
                    for page_dict in fetch_pages(
                        render_result.final_url,
                        parameter,
                        start_page=2,
                        budget=budget,
                        **fetch_kwargs,
                    ):
                        page_records = extract_html_records(
                            page_dict["content"],
                            page_dict["url"],
                        )
                        if not page_records:
                            break

                        new_records = _collect_new_records(page_records, result.records)
                        if not new_records and page_records:
                            # Stalled or duplicate pagination: stop immediately
                            break

                        rem = budget.max_records - len(result.records)
                        if rem <= 0:
                            break
                        result.records.extend(new_records[:rem])
                        result.pages_scraped += 1
                        result.requests_made += 1
                        if len(result.records) >= budget.max_records:
                            break
                        if result.pages_scraped >= budget.max_pages:
                            break


def _extract_swapcard_single_exhibitor(html: str, url: str) -> Optional[ExhibitorRecord]:
    m = re.search(r"/exhibitor/([A-Za-z0-9_=-]+)", url)
    if not m:
        return None
    exh_id = m.group(1)

    soup = BeautifulSoup(html, "html.parser")
    next_el = soup.find("script", id="__NEXT_DATA__")
    exh = None
    apollo = {}
    if next_el and next_el.string:
        try:
            data = json.loads(next_el.string)
            apollo = data.get("props", {}).get("apolloState", {})
            exh = apollo.get(f"Core_Exhibitor:{exh_id}")
            if not exh:
                for v in apollo.values():
                    if isinstance(v, dict) and v.get("_id") == exh_id:
                        exh = v
                        break
        except Exception:
            pass

    if not exh:
        q = f'''query {{ exhibitor: Core_exhibitor(_id: "{exh_id}") {{ _id name type description websiteUrl email address {{ street zipCode country city state }} phoneNumbers {{ number }} }} }}'''
        try:
            resp = fetch_url(
                "https://api.swapcard.com/graphql",
                method="POST",
                json={"query": q},
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            r_json = resp.json() if hasattr(resp, "json") else json.loads(resp.get("content", "{}"))
            exh = r_json.get("data", {}).get("exhibitor")
        except Exception:
            pass

    if not exh or not exh.get("name"):
        return None

    addr = exh.get("address") or {}
    city = addr.get("city")
    state = addr.get("state")
    country = addr.get("country")
    parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("zipCode"), addr.get("country")]
    non_empty = [p.strip() for p in parts if p and str(p).strip()]
    address_str = ", ".join(non_empty) if non_empty else None

    phones = [p["number"] for p in exh.get("phoneNumbers", []) if isinstance(p, dict) and p.get("number")]
    phone = phones[0] if phones else None

    booth = None
    for k, v in exh.items():
        if k.startswith("withEvent(") and isinstance(v, dict):
            for b in v.get("booths", []):
                ref = b.get("__ref")
                if ref and ref in apollo:
                    booth = apollo[ref].get("name")
                    if booth:
                        break

    return normalize_record(
        ExhibitorRecord(
            exhibitor_id=exh_id,
            company_name=exh["name"],
            website=exh.get("websiteUrl"),
            description=exh.get("description"),
            email=exh.get("email"),
            phone=phone,
            phones=phones,
            address=address_str,
            city=city,
            state=state,
            country=country,
            booth=booth,
            detail_url=url,
            source_url=url,
            confidence=0.98,
        )
    )


def _scrape_swapcard_directory(
    url: str,
    profile,
    result: ScrapeResult,
    budget: ScrapeBudget,
) -> bool:
    m_view = re.search(r"/event/([^/]+)/exhibitors/([A-Za-z0-9_=-]+)", url)
    if not m_view:
        return False
    event_slug = m_view.group(1)
    view_id = m_view.group(2)

    parsed_url = urlparse(url)
    base_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

    event_id = None
    if profile and profile.initial_content:
        soup = BeautifulSoup(profile.initial_content, "html.parser")
        next_el = soup.find("script", id="__NEXT_DATA__")
        if next_el and next_el.string:
            try:
                data = json.loads(next_el.string)
                event_obj = data.get("props", {}).get("event") or data.get("props", {}).get("pageProps", {}).get("event")
                if isinstance(event_obj, dict):
                    event_id = event_obj.get("id")
                if not event_id:
                    apollo = data.get("props", {}).get("apolloState", {})
                    for k, v in apollo.items():
                        if k.startswith("Core_Event:") and isinstance(v, dict) and v.get("id"):
                            event_id = v.get("id")
                            break
            except Exception:
                pass

    graphql_url = "https://api.swapcard.com/graphql"
    with_event_sub = f'(eventId: "{event_id}")' if event_id else ""
    q = f"""
    query GetSwapcardExhibitors($viewId: ID!, $endCursor: String) {{
      view: Core_eventExhibitorListView(viewId: $viewId) {{
        id
        exhibitors(cursor: {{ first: 50, after: $endCursor }}) {{
          totalCount
          pageInfo {{
            hasNextPage
            endCursor
          }}
          nodes {{
            _id
            name
            type
            description
            websiteUrl
            email
            logoUrl
            address {{
              street
              zipCode
              country
              city
              state
            }}
            phoneNumbers {{
              number
            }}
            withEvent{with_event_sub} {{
              booths {{
                name
              }}
            }}
          }}
        }}
      }}
    }}
    """

    end_cursor = None
    pages_scraped = 0

    while len(result.records) < budget.max_records and result.requests_made < budget.max_requests and pages_scraped < budget.max_pages:
        try:
            resp = fetch_url(
                graphql_url,
                method="POST",
                json={"query": q, "variables": {"viewId": view_id, "endCursor": end_cursor}},
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=budget.timeout_seconds,
            )
            result.requests_made += 1
            pages_scraped += 1

            if not resp or (hasattr(resp, "status_code") and resp.status_code != 200):
                break

            resp_data = resp.json() if hasattr(resp, "json") else json.loads(resp.get("content", "{}"))
            view_data = resp_data.get("data", {}).get("view", {})
            if not view_data:
                break

            exhibitors_conn = view_data.get("exhibitors", {})
            if not exhibitors_conn:
                break

            nodes = exhibitors_conn.get("nodes", [])
            if not nodes:
                break

            for node in nodes:
                c_name = node.get("name")
                if not c_name:
                    continue

                exh_id = node.get("_id")
                detail_url = f"{base_origin}/event/{event_slug}/exhibitor/{exh_id}" if exh_id else None

                addr = node.get("address") or {}
                city = addr.get("city")
                state = addr.get("state")
                country = addr.get("country")
                parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("zipCode"), addr.get("country")]
                non_empty = [p.strip() for p in parts if p and str(p).strip()]
                address_str = ", ".join(non_empty) if non_empty else None

                phones = [p["number"] for p in node.get("phoneNumbers", []) if isinstance(p, dict) and p.get("number")]
                phone = phones[0] if phones else None

                booths_list = [b["name"] for b in node.get("withEvent", {}).get("booths", []) if isinstance(b, dict) and b.get("name")]
                booth_str = ", ".join(booths_list) if booths_list else None

                rec = normalize_record(
                    ExhibitorRecord(
                        exhibitor_id=exh_id,
                        company_name=c_name,
                        website=node.get("websiteUrl"),
                        description=node.get("description"),
                        email=node.get("email"),
                        phone=phone,
                        phones=phones,
                        address=address_str,
                        city=city,
                        state=state,
                        country=country,
                        booth=booth_str,
                        detail_url=detail_url,
                        source_url=url,
                        confidence=0.95,
                    )
                )
                result.records.append(rec)
                if len(result.records) >= budget.max_records:
                    break

            page_info = exhibitors_conn.get("pageInfo", {})
            has_next = page_info.get("hasNextPage")
            end_cursor = page_info.get("endCursor")

            if not has_next or not end_cursor:
                break

        except Exception as exc:
            result.errors.append(f"Swapcard GraphQL error: {exc}")
            break

    result.pages_scraped = pages_scraped
    if result.records:
        result.strategy = "GRAPHQL_API"
        result.success = True
        return True
    return False


def scrape(
    url: str,
    browser_client: Optional[BrowserClient] = None,
    budget: Optional[ScrapeBudget] = None,
) -> ScrapeResult:

    started_at = datetime.now(timezone.utc)
    if budget is None:
        budget = ScrapeBudget()

    expected_pages: Optional[int] = None
    expected_records: Optional[int] = None
    budget_limited: bool = False

    result = ScrapeResult(
        source_url=url,
        final_url=url,
        started_at=started_at,
    )

    try:

        # -----------------------------------------
        # 1. DISCOVER WEBSITE
        # -----------------------------------------

        profile = discover_site(url)
        result.requests_made = 1
        result.final_url = profile.url

        # -----------------------------------------
        # 2. CHOOSE STRATEGY
        # -----------------------------------------

        # Check if URL is a single Swapcard exhibitor detail page
        if "/exhibitor/" in url.lower():
            single_rec = _extract_swapcard_single_exhibitor(profile.initial_content or "", url)
            if single_rec:
                result.records = [single_rec]
                result.pages_scraped = 1
                result.strategy = "DETAIL_PAGE"
                result.success = True
                strategy = "DETAIL_PAGE"
            else:
                strategy = choose_strategy(profile)
        # Check if URL is a Swapcard exhibitor directory list view
        elif "/exhibitors/" in url.lower() and ("Core_EventExhibitorListView" in (profile.initial_content or "") or "swapcard" in url.lower() or "cphieventplanner" in url.lower()):
            if _scrape_swapcard_directory(url, profile, result, budget):
                strategy = "GRAPHQL_API"
                result.strategy = strategy
                result.success = True
            else:
                strategy = choose_strategy(profile)
        else:
            strategy = choose_strategy(profile)

        result.strategy = strategy

        # -----------------------------------------
        # 3. EXECUTE HTML STRATEGY
        # -----------------------------------------

        if strategy in {
            "HTML",
            "HTML_DETAIL",
        }:

            initial_html = profile.initial_content or ""

            records = extract_html_records(
                initial_html,
                profile.url,
            )

            result.records.extend(records)
            result.pages_scraped = 1

            # --- PAGINATION EXECUTION ---
            if _paginate_html(profile, result, budget):
                budget_limited = True

        # -----------------------------------------
        # 4. OTHER STRATEGIES
        # -----------------------------------------

        elif strategy == "API":
            endpoint = None
            initial_payload = None

            if profile.is_json and profile.initial_content:
                import json
                try:
                    parsed_payload = json.loads(profile.initial_content)
                    eval_res = evaluate_api_payload(parsed_payload, profile.url)
                    if eval_res["valid_exhibitor_api"]:
                        endpoint = profile.url
                        initial_payload = parsed_payload
                except ValueError:
                    pass

            if not endpoint and profile.has_api_candidates:
                verified_candidates = []
                for cand in profile.api_candidates:
                    if result.requests_made >= budget.max_requests:
                        budget_limited = True
                        break

                    verification = verify_api(cand.url)
                    result.requests_made += 1

                    if verification.get("valid") and verification.get("payload") is not None:
                        verified_candidates.append((cand.url, verification.get("payload")))

                if verified_candidates:
                    ranked = rank_api_candidates(verified_candidates)
                    valid_ranked = [item for item in ranked if item["valid_exhibitor_api"]]
                    if valid_ranked:
                        endpoint = valid_ranked[0]["url"]
                        initial_payload = valid_ranked[0]["payload"]

            if not endpoint:
                # If discovered API candidates were not valid exhibitor APIs, fall back to HTML extraction
                if profile.is_html and profile.initial_content:
                    html_records = extract_html_records(
                        profile.initial_content,
                        profile.url,
                    )
                    if html_records:
                        result.records.extend(html_records[:budget.max_records])
                        result.pages_scraped = 1
                        result.strategy = "HTML"
                        result.success = True
                        if _paginate_html(profile, result, budget):
                            budget_limited = True
                if not result.records:
                    result.errors.append("No valid API endpoint found.")
                    result.success = False
            else:
                if isinstance(initial_payload, dict):
                    for k in ["total", "total_count", "totalCount", "TotalRecords", "totalRecords", "total_records", "count"]:
                        val = initial_payload.get(k)
                        if isinstance(val, int) and val > 0:
                            expected_records = val
                            break

                # 1. Extract records from the initial payload
                context_url = profile.url if "api." in endpoint.lower() else endpoint
                records = extract_api_records(initial_payload, context_url)
                result.records.extend(records[:budget.max_records])
                result.pages_scraped = 1

                # If records found and budget not reached, paginate deterministically
                if records and len(result.records) < budget.max_records:
                    # 2. Determine pagination parameter and step deterministically based on evidence
                    pagination_config = determine_api_pagination(
                        endpoint=endpoint,
                        initial_payload=initial_payload,
                        pagination_signals=profile.pagination_signals if profile.has_pagination else None,
                        records_count=len(records),
                    )

                    parameter = pagination_config["parameter"]
                    step = pagination_config["step"]
                    start_page = pagination_config["start_page"]
                    raw_total_pages = pagination_config.get("total_pages")
                    if raw_total_pages is not None:
                        expected_pages = raw_total_pages
                        total_pages = min(raw_total_pages, budget.max_pages)
                    else:
                        total_pages = budget.max_pages

                    # 3. Pagination execution respecting remaining request budget
                    remaining_requests = max(
                        0,
                        budget.max_requests - result.requests_made
                    )

                    if remaining_requests <= 0 and (
                        (expected_pages and expected_pages > result.pages_scraped)
                        or (expected_records and expected_records > len(result.records))
                    ):
                        budget_limited = True

                    if remaining_requests > 0:
                        for page_dict in fetch_api_pages(
                            endpoint,
                            parameter,
                            budget=budget,
                            start_page=start_page,
                            step=step,
                            max_requests=remaining_requests,
                        ):
                            result.requests_made += 1

                            if not page_dict.get("valid_json"):
                                break

                            page_payload = page_dict["payload"]
                            page_records = extract_api_records(page_payload, context_url)

                            if not page_records:
                                break

                            new_records = _collect_new_records(page_records, result.records)
                            if not new_records and page_records:
                                # Stalled or duplicate pagination: stop immediately
                                break

                            rem = budget.max_records - len(result.records)
                            if rem <= 0:
                                budget_limited = True
                                break

                            result.records.extend(new_records[:rem])
                            result.pages_scraped += 1

                            if len(result.records) >= budget.max_records:
                                if expected_records and expected_records > len(result.records):
                                    budget_limited = True
                                break

                            if result.pages_scraped >= total_pages:
                                if expected_pages and expected_pages > result.pages_scraped:
                                    budget_limited = True
                                break

        elif strategy == "IFRAME":

            if not profile.iframe_urls:
                result.errors.append("No iframe URLs found.")
                result.success = False
            else:
                for iframe_url in profile.iframe_urls:
                    if result.requests_made >= budget.max_requests:
                        break
                    if len(result.records) >= budget.max_records:
                        break

                    try:
                        iframe_resp = fetch_url(
                            iframe_url,
                            timeout=budget.timeout_seconds,
                        )
                    except Exception as exc:
                        result.errors.append(f"Failed to fetch iframe {iframe_url}: {exc}")
                        continue

                    result.requests_made += 1

                    content = (
                        iframe_resp.content
                        if hasattr(iframe_resp, "content")
                        else iframe_resp.get("content", "")
                    )
                    content_type = (
                        iframe_resp.content_type
                        if hasattr(iframe_resp, "content_type")
                        else iframe_resp.get("headers", {}).get("Content-Type", "")
                    ).lower()
                    final_url = (
                        iframe_resp.final_url
                        if hasattr(iframe_resp, "final_url")
                        else iframe_resp.get("url", iframe_url)
                    )

                    is_json_iframe = "application/json" in content_type
                    iframe_payload = None

                    if is_json_iframe:
                        try:
                            if hasattr(iframe_resp, "json"):
                                iframe_payload = iframe_resp.json()
                            else:
                                import json
                                iframe_payload = json.loads(content)
                        except Exception:
                            is_json_iframe = False

                    if not is_json_iframe:
                        stripped = content.strip()
                        if (stripped.startswith("{") and stripped.endswith("}")) or (
                            stripped.startswith("[") and stripped.endswith("]")
                        ):
                            try:
                                import json
                                iframe_payload = json.loads(stripped)
                                if find_record_collection(iframe_payload):
                                    is_json_iframe = True
                            except Exception:
                                is_json_iframe = False

                    # ------------------------------------------------
                    # Case A: JSON/API iframe response
                    # ------------------------------------------------
                    if is_json_iframe and iframe_payload is not None:
                        api_records = extract_api_records(iframe_payload, final_url)
                        if api_records:
                            remaining = budget.max_records - len(result.records)
                            result.records.extend(api_records[:remaining])
                            result.pages_scraped += 1

                            if len(result.records) < budget.max_records:
                                pagination_config = determine_api_pagination(
                                    endpoint=final_url,
                                    initial_payload=iframe_payload,
                                    records_count=len(api_records),
                                )
                                parameter = pagination_config["parameter"]
                                step = pagination_config["step"]
                                start_page = pagination_config["start_page"]
                                total_pages = pagination_config["total_pages"]
                                if total_pages is not None:
                                    total_pages = min(total_pages, budget.max_pages)
                                else:
                                    total_pages = budget.max_pages

                                remaining_requests = max(
                                    0,
                                    budget.max_requests - result.requests_made
                                )

                                if remaining_requests > 0:
                                    for page_dict in fetch_api_pages(
                                        final_url,
                                        parameter,
                                        budget=budget,
                                        start_page=start_page,
                                        step=step,
                                        max_requests=remaining_requests,
                                    ):
                                        result.requests_made += 1
                                        if not page_dict.get("valid_json"):
                                            break
                                        page_records = extract_api_records(
                                            page_dict["payload"], page_dict["url"]
                                        )
                                        if not page_records:
                                            break
                                        new_records = _collect_new_records(page_records, result.records)
                                        if not new_records and page_records:
                                            # Stalled or duplicate pagination: stop immediately
                                            break
                                        rem = budget.max_records - len(result.records)
                                        if rem <= 0:
                                            break
                                        result.records.extend(new_records[:rem])
                                        result.pages_scraped += 1
                                        if len(result.records) >= budget.max_records:
                                            break
                                        if result.pages_scraped >= total_pages:
                                            break

                    # ------------------------------------------------
                    # Case B: HTML iframe response
                    # ------------------------------------------------
                    else:
                        html_records = extract_html_records(
                            content,
                            final_url,
                        )
                        if html_records:
                            remaining = budget.max_records - len(result.records)
                            result.records.extend(html_records[:remaining])
                            result.pages_scraped += 1

                            # HTML Pagination inside iframe
                            iframe_html_info = analyze_html(
                                content,
                                final_url,
                            )
                            iframe_pagination_links = [
                                link["url"] for link in iframe_html_info["links"]
                            ]
                            iframe_pagination_info = detect_pagination(
                                final_url,
                                iframe_pagination_links,
                            )

                            if (
                                iframe_pagination_info["has_pagination"]
                                and iframe_pagination_info["signals"]
                                and len(result.records) < budget.max_records
                            ):
                                signal = iframe_pagination_info["signals"][0]
                                parameter = signal.get("parameter")
                                path_pattern = signal.get("path_pattern")
                                total_pages = signal.get("total_pages")
                                if parameter:
                                    fetch_kwargs = {}
                                    if path_pattern is not None:
                                        fetch_kwargs["path_pattern"] = path_pattern
                                    if total_pages is not None:
                                        fetch_kwargs["total_pages"] = total_pages
                                    for page_dict in fetch_pages(
                                        final_url,
                                        parameter,
                                        start_page=2,
                                        budget=budget,
                                        **fetch_kwargs,
                                    ):
                                        page_records = extract_html_records(
                                            page_dict["content"],
                                            page_dict["url"],
                                        )
                                        if not page_records:
                                            break

                                        new_records = _collect_new_records(page_records, result.records)
                                        if not new_records and page_records:
                                            # Stalled or duplicate pagination: stop immediately
                                            break

                                        rem = budget.max_records - len(result.records)
                                        if rem <= 0:
                                            break
                                        result.records.extend(new_records[:rem])
                                        result.pages_scraped += 1
                                        result.requests_made += 1
                                        if len(result.records) >= budget.max_records:
                                            break

        elif strategy == "BROWSER":

            _execute_browser_strategy(
                profile.url,
                profile,
                result,
                budget,
                browser_client,
            )

        else:

            result.errors.append(
                "No supported scraping strategy found."
            )

        # -----------------------------------------
        # 4b. BROWSER ESCALATION FALLBACK
        # -----------------------------------------
        if (
            not result.records
            and strategy != "BROWSER"
            and should_use_browser(profile, result)
        ):
            result.fallback_reason = (
                "Deterministic extraction yielded 0 records and JS evidence was detected."
            )
            _execute_browser_strategy(
                profile.url,
                profile,
                result,
                budget,
                browser_client,
            )
            if result.records:
                result.success = True
                result.errors = [
                    e for e in result.errors
                    if e not in {
                        "No valid API endpoint found.",
                        "No supported scraping strategy found.",
                    }
                ]

        # -----------------------------------------
        # 5. DEDUPLICATE
        # -----------------------------------------

        result.records, duplicates_removed = deduplicate_with_stats(
            result.records
        )
        result.duplicates_removed = duplicates_removed

        # -----------------------------------------
        # 5b. DETAIL URL ASSOCIATION & BOUNDED ENRICHMENT
        # -----------------------------------------

        html_for_details = getattr(result, "_rendered_html", None) or (profile.initial_content if profile else "")
        if html_for_details and result.records:
            associate_detail_urls(result.records, html_for_details, result.final_url or url)

        should_enrich = (
            getattr(budget, "enrich_details", False)
            or result.strategy in {"BROWSER", "BROWSER_API", "BROWSER_HTML"}
        )

        # Enrich records missing key fields within remaining request budget
        if should_enrich and result.records and result.requests_made < budget.max_requests:
            result.records, detail_reqs = enrich_records(
                result.records,
                budget=budget,
                current_requests=result.requests_made,
                max_enrich=getattr(budget, "max_detail_requests", None),
            )
            result.requests_made += detail_reqs

        # -----------------------------------------
        # 5c. CLEAN COMMON FOOTER / ORGANIZER CONTACTS
        # -----------------------------------------
        html_for_cleanup = getattr(result, "_rendered_html", None) or (profile.initial_content if profile else "")
        result.records = clean_global_organizer_contacts(
            result.records,
            source_html=html_for_cleanup,
            source_url=result.final_url or url,
        )

        # -----------------------------------------
        # 6. QUALITY EVALUATION
        # -----------------------------------------

        quality = evaluate_extraction_quality(
            records=result.records,
            duplicates_removed=result.duplicates_removed,
            pages_scraped=result.pages_scraped,
            expected_pages=expected_pages,
            expected_records=expected_records,
            budget_limited=budget_limited,
        )
        result.quality = quality
        result.quality_status = quality.quality_status.value
        result.quality_score = quality.quality_score

        # -----------------------------------------
        # 7. SUCCESS
        # -----------------------------------------

        result.success = (
            len(result.records) > 0
        )

    except Exception as exc:

        result.success = False

        result.errors.append(
            str(exc)
        )

        quality = evaluate_extraction_quality(
            records=result.records,
            duplicates_removed=result.duplicates_removed,
            pages_scraped=result.pages_scraped,
            expected_pages=expected_pages,
            expected_records=expected_records,
            budget_limited=budget_limited,
            scrape_error=str(exc),
        )
        result.quality = quality
        result.quality_status = quality.quality_status.value
        result.quality_score = quality.quality_score

    result.finished_at = datetime.now(
        timezone.utc
    )
    result.duration_seconds = round(
        (result.finished_at - result.started_at).total_seconds(), 4
    )

    return result