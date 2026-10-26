from app.discovery.strategy import choose_strategy
from app.models.site_profile import SiteProfile


def test_api_strategy():

    profile = SiteProfile(
        url="https://example.com",
        is_json=True
    )

    assert choose_strategy(profile) == "API"


def test_api_candidate_strategy():

    profile = SiteProfile(
        url="https://example.com",
        has_api_candidates=True
    )

    assert choose_strategy(profile) == "API"


def test_iframe_strategy():

    profile = SiteProfile(
        url="https://example.com",
        has_iframe=True
    )

    assert choose_strategy(profile) == "IFRAME"


def test_html_detail_strategy():

    profile = SiteProfile(
        url="https://example.com",
        has_detail_links=True
    )

    assert choose_strategy(profile) == "HTML_DETAIL"


def test_html_strategy():

    profile = SiteProfile(
        url="https://example.com",
        has_repeated_elements=True
    )

    assert choose_strategy(profile) == "HTML"


def test_browser_strategy():

    profile = SiteProfile(
        url="https://example.com",
        javascript_required=True
    )

    assert choose_strategy(profile) == "BROWSER"


def test_unknown_strategy():

    profile = SiteProfile(
        url="https://example.com"
    )

    assert choose_strategy(profile) == "UNKNOWN"