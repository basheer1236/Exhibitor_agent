import re
from typing import Optional
from app.models.site_profile import SiteProfile
from app.models.scrape_result import ScrapeResult


def choose_strategy(profile: SiteProfile) -> str:

    # 1. Direct JSON/API response
    if profile.is_json:
        return "API"

    # 2. API candidates discovered in the page
    if profile.has_api_candidates:
        return "API"

    # 3. Iframe-based directory
    if profile.has_iframe:
        return "IFRAME"

    # 4. Detail-page based directory
    if profile.has_detail_links:
        return "HTML_DETAIL"

    # 5. Repeated HTML elements
    if profile.has_repeated_elements:
        return "HTML"

    # 6. JavaScript/browser fallback
    if profile.javascript_required:
        return "BROWSER"

    return "UNKNOWN"


def should_use_browser(
    profile: SiteProfile,
    result: Optional[ScrapeResult] = None,
) -> bool:
    """
    Determine whether to escalate to browser rendering.
    Returns True ONLY if deterministic extraction produced zero records
    AND there is concrete evidence that JavaScript rendering is required.
    """
    # If deterministic extraction already succeeded, never use browser
    if result is not None and len(result.records) > 0:
        return False

    # Evidence 1: Discovery flagged javascript_required
    if profile.javascript_required:
        return True

    # Evidence 2: Site classified as JAVASCRIPT or BROWSER
    if profile.site_type in {"JAVASCRIPT", "BROWSER"}:
        return True

    # Evidence 3: Static HTML contains SPA application shell structure with script tags
    if profile.is_html and profile.initial_content:
        content_lower = profile.initial_content.lower()
        has_scripts = "<script" in content_lower
        has_spa_shell = any(
            pattern in content_lower
            for pattern in [
                'id="root"',
                'id="app"',
                'id="__next"',
                'id="__nuxt"',
                '<app-root',
                'id="application"',
                '/_next/',
                '__next_f',
                '/_nuxt/',
                '/static/chunks/',
                '-root"',
                'data-config=',
                'data-component=',
                'abstractcomponent',
            ]
        )
        if has_scripts and has_spa_shell:
            return True

        # Evidence 4: Static HTML contains an empty directory table shell
        if has_scripts and "<table" in content_lower:
            has_dir_keywords = any(
                k in content_lower
                for k in ["exhibitor", "company", "stall", "booth", "stand"]
            )
            has_empty_tbody = (
                "<tbody></tbody>" in content_lower.replace(" ", "")
                or "<tbody>\n</tbody>" in content_lower
                or "<tbody id=" in content_lower
            )
            has_empty_table = (
                bool(re.search(r"<table[^>]*>\s*</table>", content_lower))
                or bool(re.search(r"<table[^>]*id=[\"'][^\"']*(?:exlist|exhibitor|stall|company|dir)[^\"']*[\"']", content_lower))
            )
            if has_dir_keywords and (has_empty_tbody or has_empty_table):
                return True

        # Evidence 5: Dynamic script embed or iframe indicators
        if has_scripts:
            has_embed_signals = any(
                pattern in content_lower
                for pattern in [
                    "iframeurl=",
                    "iframe_url=",
                    "embedurl=",
                    "embed_url=",
                    "data-iframe=",
                    "data-src=",
                    "pre-fair",
                    "function.js",
                    "sweet-daifuku",
                    "eventstrat",
                ]
            )
            if has_embed_signals:
                return True

            # Evidence 6: URL indicates an exhibitor directory where deterministic extraction yielded 0 records
            url_lower = profile.url.lower()
            has_dir_intent = any(
                k in url_lower
                for k in ["exhibitor", "directory", "participant", "supplier", "vendor", "stall", "fair", "expo"]
            )
            if has_dir_intent:
                return True

    return False