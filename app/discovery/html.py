from bs4 import BeautifulSoup
from urllib.parse import urljoin


def analyze_html(html: str, base_url: str):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # -----------------------------------------
    # IFRAME DETECTION
    # -----------------------------------------

    DISALLOWED_IFRAME_PATTERNS = {
        "googletagmanager.com",
        "google-analytics.com",
        "doubleclick.net",
        "facebook.com",
        "recaptcha",
        "hcaptcha",
        "clarity.ms",
        "hotjar.com",
        "intercom.io",
        "tawk.to",
        "zendesk.com",
        "youtube.com",
        "vimeo.com",
    }

    iframe_urls = []
    for iframe in soup.find_all("iframe", src=True):
        if iframe.find_parent("noscript"):
            continue
        src = iframe["src"].strip()
        if not src or src.startswith("javascript:") or src.startswith("about:"):
            continue
        full_src = urljoin(base_url, src)
        src_lower = full_src.lower()
        if any(pattern in src_lower for pattern in DISALLOWED_IFRAME_PATTERNS):
            continue
        iframe_urls.append(full_src)

    # Also discover embedded application URLs passed via script loaders
    # e.g. <script src=".../function.js?iframeUrl=https://exhibitor.ihexpo.com/pre-fair-directory">
    from urllib.parse import parse_qs, urlparse
    for s in soup.find_all("script", src=True):
        s_src = s["src"].strip()
        s_src_lower = s_src.lower()
        if "iframeurl=" in s_src_lower or "iframe_url=" in s_src_lower or "embedurl=" in s_src_lower:
            parsed_s = urlparse(s_src)
            qs = parse_qs(parsed_s.query)
            for param_key in ["iframeUrl", "iframeurl", "iframe_url", "embedUrl", "embed_url"]:
                if param_key in qs and qs[param_key]:
                    target = qs[param_key][0]
                    if target.startswith(("http://", "https://")):
                        iframe_urls.append(target)

    # -----------------------------------------
    # LINK DETECTION
    # -----------------------------------------

    links = []

    for link in soup.find_all(
        "a",
        href=True
    ):

        text = link.get_text(
            " ",
            strip=True
        )

        url = urljoin(
            base_url,
            link["href"]
        )

        links.append({
            "text": text,
            "url": url
        })

    # -----------------------------------------
    # SCRIPT DETECTION
    # -----------------------------------------

    scripts = [
        script.get("src")
        for script in soup.find_all("script")
        if script.get("src")
    ]

    return {
        "iframe_urls": iframe_urls,
        "links": links,
        "scripts": scripts,
        "has_iframe": len(iframe_urls) > 0,
    }