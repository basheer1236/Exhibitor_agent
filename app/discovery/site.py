from urllib.parse import urlparse

from app.discovery.http import fetch_url
from app.discovery.html import analyze_html
from app.discovery.pagination import detect_pagination
from app.discovery.api import find_api_candidates
from app.discovery.repeated_elements import find_repeated_elements

from app.guardrails.url import validate_url

from app.models.site_profile import (
    SiteProfile,
    ApiCandidate,
)


def discover_site(url: str) -> SiteProfile:

    # -----------------------------------------
    # 1. VALIDATE URL
    # -----------------------------------------

    url = validate_url(url)

    # -----------------------------------------
    # 2. FETCH WEBSITE
    # -----------------------------------------

    response = fetch_url(url)

    headers = response.get("headers", {}) if isinstance(response, dict) else getattr(response, "headers", {})
    content_type = (
        getattr(response, "content_type", None)
        or (response.get("content_type") if isinstance(response, dict) else None)
        or headers.get("content-type")
        or headers.get("Content-Type")
        or ""
    ).lower()

    raw_content = (
        getattr(response, "content", "")
        if hasattr(response, "content")
        else (response.get("content", "") if isinstance(response, dict) else "")
    )

    is_html = (
        "text/html" in content_type
        or "<html" in raw_content[:1000].lower()
        or "<!doctype html" in raw_content[:1000].lower()
    )
    is_json = (
        "application/json" in content_type
        or (raw_content.strip().startswith(("{", "[")) and raw_content.strip().endswith(("}", "]")))
    )

    profile = SiteProfile(
        url=response["url"],
        status_code=response["status_code"],
        content_type=content_type,
        is_html=is_html,
        is_json=is_json,
        initial_content=raw_content,
    )

    # -----------------------------------------
    # 3. JSON / API RESPONSE
    # -----------------------------------------

    if is_json:

        profile.site_type = "API"

        return profile

    # -----------------------------------------
    # 4. ONLY HTML NEEDS HTML ANALYSIS
    # -----------------------------------------

    if not is_html:

        profile.site_type = "UNKNOWN"

        return profile

    html = response["content"]

    base_url = response["url"]

    # -----------------------------------------
    # 5. HTML STRUCTURE ANALYSIS
    # -----------------------------------------

    html_info = analyze_html(
        html,
        base_url,
    )

    # -----------------------------------------
    # 6. IFRAME DETECTION
    # -----------------------------------------

    profile.has_iframe = (
        html_info["has_iframe"]
    )

    profile.iframe_urls = (
        html_info["iframe_urls"]
    )

    # -----------------------------------------
    # 7. API DETECTION
    # -----------------------------------------

    api_urls = find_api_candidates(
        html,
        base_url,
    )

    profile.api_candidates = [
        ApiCandidate(
            url=url,
        )
        for url in api_urls
    ]

    profile.has_api_candidates = (
        len(api_urls) > 0
    )

    # -----------------------------------------
    # 8. PAGINATION DETECTION
    # -----------------------------------------
    #
    # analyze_html() returns:
    #
    # [
    #     {
    #         "text": "...",
    #         "url": "..."
    #     }
    # ]
    #
    # detect_pagination() expects:
    #
    # [
    #     "https://example.com?page=2",
    #     "https://example.com?page=3"
    # ]
    #
    # Therefore we extract only the URLs.
    # -----------------------------------------

    pagination_links = [
        link["url"]
        for link in html_info["links"]
    ]

    pagination_info = detect_pagination(
        base_url,
        pagination_links,
        html=raw_content,
    )

    profile.has_pagination = (
        pagination_info["has_pagination"]
    )

    profile.pagination_signals = (
        pagination_info["signals"]
    )

    # -----------------------------------------
    # 9. DETAIL-LINK DETECTION
    # -----------------------------------------

    detail_links = []
    base_host = urlparse(base_url).netloc.lower()

    for link in html_info["links"]:

        text = link["text"]
        link_url = link["url"]
        parsed_link = urlparse(link_url)
        link_host = parsed_link.netloc.lower()

        # Detail links must be internal to the target directory (same host or relative)
        if link_host and base_host and link_host != base_host:
            continue

        # Skip self-referential or fragment links pointing to current page
        clean_link = link_url.split("#")[0].rstrip("/")
        clean_base = base_url.split("#")[0].rstrip("/")
        if clean_link == clean_base:
            continue

        path_lower = parsed_link.path.lower()
        if any(nav in path_lower for nav in [
            "enquiry", "inquiry", "feedback", "newsletter", "brochure",
            "floor-plan", "floorplan", "guidelines", "manual",
            "exhibitors-profile", "exhibitor-profile", "exhibitors-list", "exhibitor-list",
            "cdn-cgi", "email-protection",
        ]):
            continue

        text_lower = text.lower()
        if any(nav in text_lower for nav in [
            "become an", "become a", "feedback", "enquiry", "inquiry",
            "newsletter", "brochure", "guidelines", "manual",
            "exhibitors list", "exhibitor list", "exhibitor profile", "exhibitors profile",
            "[email",
        ]):
            continue

        keywords = [
            "exhibitor",
            "company",
            "supplier",
            "vendor",
            "manufacturer",
            "profile",
            "member",
            "brand",
            "stand",
            "booth",
        ]

        combined = (
            f"{text.lower()} "
            f"{parsed_link.path.lower()}"
        )

        if any(
            keyword in combined
            for keyword in keywords
        ):

            detail_links.append(
                link_url
            )

    profile.detail_links = list(
        dict.fromkeys(
            detail_links
        )
    )

    profile.has_detail_links = (
        len(profile.detail_links) > 0
    )

    # -----------------------------------------
    # 10. REPEATED HTML ELEMENT DETECTION
    # -----------------------------------------

    repeated_elements = (
        find_repeated_elements(html)
    )

    profile.repeated_elements = (
        repeated_elements
    )

    profile.has_repeated_elements = (
        len(repeated_elements) > 0
    )

    # -----------------------------------------
    # 11. BASIC JAVASCRIPT DETECTION
    # -----------------------------------------

    profile.javascript_required = (
        len(html_info["scripts"]) > 0
        and not profile.has_detail_links
        and not profile.has_repeated_elements
        and not profile.has_api_candidates
    )

    # -----------------------------------------
    # 12. BASIC SITE CLASSIFICATION
    # -----------------------------------------

    if profile.has_api_candidates:

        profile.site_type = (
            "API_DIRECTORY"
        )

    elif profile.has_detail_links:

        profile.site_type = (
            "HTML_DIRECTORY"
        )

    elif profile.has_repeated_elements:

        profile.site_type = (
            "HTML_LIST"
        )

    elif profile.has_iframe:

        profile.site_type = (
            "IFRAME"
        )

    elif profile.javascript_required:

        profile.site_type = (
            "JAVASCRIPT"
        )

    else:

        profile.site_type = (
            "UNKNOWN"
        )

    return profile