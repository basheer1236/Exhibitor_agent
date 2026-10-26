import pytest
from unittest.mock import MagicMock, patch

from app.agents.universal_scraper import scrape
from app.browser import (
    BrowserClient,
    BrowserRenderResult,
    BrowserError,
    BrowserUnavailableError,
    BrowserNavigationError,
    BrowserTimeoutError,
    BrowserSecurityError,
    BrowserRequestLimitError,
    BrowserPageSizeError,
)
from app.models.site_profile import SiteProfile
from app.models.scrape_budget import ScrapeBudget
from app.models.exhibitor import ExhibitorRecord
from app.discovery.strategy import should_use_browser


# ==============================================================================
# 1. Fallback Decision Logic & Triggering
# ==============================================================================

def test_fallback_invoked_when_deterministic_fails_and_js_evidence():
    """Browser fallback is invoked when deterministic extraction yields 0 records and JS evidence exists."""
    profile = SiteProfile(
        url="https://example.com/exhibitors",
        is_html=True,
        initial_content='<div id="root"></div><script src="/bundle.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/exhibitors",
        final_url="https://example.com/exhibitors",
        status_code=200,
        rendered_html="""
        <html>
            <body>
                <a href="/exhibitor/alpha">Alpha Tech</a>
                <a href="/exhibitor/beta">Beta Corp</a>
            </body>
        </html>
        """,
        requests_made=1,
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/exhibitors", browser_client=mock_client)

    assert mock_client.render.called
    assert result.success is True
    assert len(result.records) == 2
    assert result.strategy == "BROWSER_HTML"
    assert result.records[0].company_name == "Alpha Tech"
    assert result.records[1].company_name == "Beta Corp"


def test_browser_not_invoked_when_api_succeeds(monkeypatch):
    """Browser fallback is NOT invoked when API extraction succeeds."""
    mock_client = MagicMock(spec=BrowserClient)

    def mock_fetch(url, *args, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "content": '{"items": [{"name": "API Exhibitor"}]}',
        }

    monkeypatch.setattr("app.discovery.http.fetch_url", mock_fetch)
    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch)

    result = scrape("https://example.com/api/exhibitors", browser_client=mock_client)

    assert not mock_client.render.called
    assert result.strategy == "API"
    assert result.success is True
    assert len(result.records) == 1
    assert result.records[0].company_name == "API Exhibitor"


def test_browser_not_invoked_when_static_html_succeeds(monkeypatch):
    """Browser fallback is NOT invoked when static HTML extraction succeeds."""
    mock_client = MagicMock(spec=BrowserClient)

    html = """
    <html>
        <body>
            <a href="/exhibitor/static1">Static One</a>
        </body>
    </html>
    """

    def mock_fetch(url, *args, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": html,
        }

    monkeypatch.setattr("app.discovery.http.fetch_url", mock_fetch)
    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch)

    result = scrape("https://example.com/exhibitors", browser_client=mock_client)

    assert not mock_client.render.called
    assert result.strategy == "HTML_DETAIL"
    assert result.success is True
    assert len(result.records) == 1
    assert result.records[0].company_name == "Static One"


def test_browser_not_invoked_when_zero_records_and_no_js_evidence():
    """Browser fallback is NOT invoked when 0 records are found on static page with no JS evidence."""
    profile = SiteProfile(
        url="https://example.com/empty",
        is_html=True,
        initial_content="<html><body><p>No exhibitors here</p></body></html>",
        javascript_required=False,
        site_type="UNKNOWN",
    )

    mock_client = MagicMock(spec=BrowserClient)

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/empty", browser_client=mock_client)

    assert not mock_client.render.called
    assert result.success is False
    assert len(result.records) == 0


def test_should_use_browser_decision_tree():
    """Verify should_use_browser strictly verifies 0 records AND JS evidence."""
    # When records exist, never use browser
    p = SiteProfile(url="https://example.com", javascript_required=True)
    res_with_records = MagicMock()
    res_with_records.records = [ExhibitorRecord(company_name="Acme", source_url="https://example.com")]
    assert should_use_browser(p, res_with_records) is False

    # When 0 records and javascript_required
    res_empty = MagicMock()
    res_empty.records = []
    assert should_use_browser(p, res_empty) is True

    # When 0 records and SPA shell markup with scripts
    spa_profile = SiteProfile(
        url="https://example.com",
        is_html=True,
        initial_content='<div id="root"></div><script src="main.js"></script>',
        javascript_required=False,
    )
    assert should_use_browser(spa_profile, res_empty) is True

    # When 0 records and no JS evidence
    plain_profile = SiteProfile(
        url="https://example.com",
        is_html=True,
        initial_content="<div>Static text without scripts</div>",
        javascript_required=False,
    )
    assert should_use_browser(plain_profile, res_empty) is False


# ==============================================================================
# 2. Rendering and Extraction
# ==============================================================================

def test_js_generated_html_rendered_and_extracted():
    """Rendered HTML is passed through the existing HTML extractor."""
    rendered_dom = """
    <html>
        <body>
            <div class="exhibitor-list">
                <a href="/company/cyberdyne">Cyberdyne Systems</a>
                <a href="/company/weyland">Weyland-Yutani</a>
            </div>
        </body>
    </html>
    """
    profile = SiteProfile(
        url="https://example.com/portal",
        is_html=True,
        initial_content='<div id="app"></div><script src="/app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/portal",
        final_url="https://example.com/portal",
        status_code=200,
        rendered_html=rendered_dom,
        requests_made=1,
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/portal", browser_client=mock_client)

    assert result.success is True
    assert result.strategy == "BROWSER_HTML"
    names = {r.company_name for r in result.records}
    assert "Cyberdyne Systems" in names
    assert "Weyland-Yutani" in names


# ==============================================================================
# 3. Security Guardrails (BrowserClient)
# ==============================================================================

def test_localhost_initial_url_blocked():
    """Browser navigation directly to localhost or 127.0.0.1 is blocked."""
    client = BrowserClient()
    with pytest.raises(BrowserSecurityError):
        client.render("http://localhost:8080/directory")

    with pytest.raises(BrowserSecurityError):
        client.render("http://127.0.0.1:3000/directory")


def test_private_ip_initial_url_blocked():
    """Browser navigation to private IPv4 ranges is blocked."""
    client = BrowserClient()
    for ip in ["192.168.1.1", "10.0.0.50", "172.16.5.1"]:
        with pytest.raises(BrowserSecurityError):
            client.render(f"http://{ip}/directory")


def test_redirect_to_private_ip_blocked():
    """Redirect to private IP or localhost raises BrowserSecurityError."""
    client = BrowserClient()
    budget = ScrapeBudget()

    def mock_resolve(host, port):
        if host in {"127.0.0.1", "localhost"} or "192.168" in str(host):
            from app.guardrails.url import SSRFError
            raise SSRFError(f"Blocked private address: {host}")
        return "93.184.216.34"

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", side_effect=mock_resolve):

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            def fake_on(event, handler):
                if event == "response":
                    mock_resp = MagicMock()
                    mock_resp.request.is_navigation_request.return_value = True
                    mock_resp.status = 302
                    mock_resp.url = "https://example.com"
                    mock_resp.headers = {"location": "http://127.0.0.1/admin"}
                    handler(mock_resp)

            mock_page.on.side_effect = fake_on

            with pytest.raises(BrowserSecurityError) as exc_info:
                client.render("https://example.com", budget=budget)

            assert "Redirect to unsafe destination blocked" in str(exc_info.value) or "127.0.0.1" in str(exc_info.value)


def test_unsafe_browser_subresource_blocked():
    """Unsafe subresources (private IPs) are intercepted and aborted."""
    client = BrowserClient()
    budget = ScrapeBudget(max_requests=10)

    def mock_resolve(host, port):
        if "192.168" in str(host):
            from app.guardrails.url import SSRFError
            raise SSRFError(f"Private address blocked: {host}")
        return "93.184.216.34"

    with patch("app.browser.client.validate_url", side_effect=lambda u: u), \
         patch("app.browser.client.resolve_and_validate_host", side_effect=mock_resolve):

        mock_page = MagicMock()
        mock_page.content.return_value = "<html><body>Safe Content</body></html>"
        mock_page.url = "https://example.com"
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            routes_registered = []

            def fake_route(pattern, handler):
                routes_registered.append(handler)

            mock_page.route.side_effect = fake_route

            def fake_goto(*args, **kwargs):
                handler = routes_registered[0]
                mock_route = MagicMock()
                mock_route.request.url = "http://192.168.1.1/internal.js"
                mock_route.request.resource_type = "script"
                mock_route.request.is_navigation_request.return_value = False
                handler(mock_route)
                mock_route.abort.assert_called_with("blockedbyclient")

            mock_page.goto.side_effect = fake_goto

            result = client.render("https://example.com", budget=budget)
            assert any("192.168.1.1" in err or "unsafe" in err.lower() for err in result.errors)


# ==============================================================================
# 4. Budget, Timeout, and Limits
# ==============================================================================

def test_browser_timeout_handled():
    """Browser navigation timeout raises BrowserTimeoutError."""
    client = BrowserClient()
    budget = ScrapeBudget(timeout_seconds=1)

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", return_value="93.184.216.34"):

        mock_page = MagicMock()
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        mock_page.goto.side_effect = PlaywrightTimeoutError("Navigation timed out")

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            with pytest.raises(BrowserTimeoutError):
                client.render("https://example.com", budget=budget)


def test_browser_request_budget_enforced():
    """Browser request budget limits total requests made."""
    client = BrowserClient()
    zero_budget = ScrapeBudget(max_requests=0)

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", return_value="93.184.216.34"):

        with pytest.raises(BrowserRequestLimitError):
            client.render("https://example.com", budget=zero_budget)


def test_page_size_limit_enforced():
    """Rendered HTML exceeding max_size_bytes raises BrowserPageSizeError."""
    client = BrowserClient()
    budget = ScrapeBudget()

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", return_value="93.184.216.34"):

        mock_page = MagicMock()
        mock_page.content.return_value = "<html>" + ("x" * 200) + "</html>"
        mock_page.url = "https://example.com"
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            with pytest.raises(BrowserPageSizeError):
                client.render("https://example.com", budget=budget, max_size_bytes=50)


# ==============================================================================
# 5. Lifecycle and Cleanup
# ==============================================================================

def test_browser_cleanup_on_success():
    """Browser resources (page, context, browser, playwright) are closed on success."""
    client = BrowserClient()
    budget = ScrapeBudget()

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", return_value="93.184.216.34"):

        mock_page = MagicMock()
        mock_page.content.return_value = "<html><body>OK</body></html>"
        mock_page.url = "https://example.com"
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            client.render("https://example.com", budget=budget)

        mock_page.close.assert_called_once()
        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()


def test_browser_cleanup_on_failure():
    """Browser resources are guaranteed to close even on unexpected failure."""
    client = BrowserClient()
    budget = ScrapeBudget()

    with patch("app.browser.client.validate_url", return_value="https://example.com"), \
         patch("app.browser.client.resolve_and_validate_host", return_value="93.184.216.34"):

        mock_page = MagicMock()
        mock_page.goto.side_effect = RuntimeError("Crash during navigation")
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch.return_value = mock_browser

        with patch("playwright.sync_api.sync_playwright") as mock_sync:
            mock_sync.return_value.start.return_value = mock_playwright

            with pytest.raises(BrowserNavigationError):
                client.render("https://example.com", budget=budget)

        mock_page.close.assert_called_once()
        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()


# ==============================================================================
# 6. Pagination and Budgets in Scraper
# ==============================================================================

def test_deterministic_rendered_pagination():
    """Deterministic pagination links in the rendered DOM are followed."""
    rendered_dom = """
    <html>
        <body>
            <a href="/exhibitor/item1">Item 1</a>
            <div class="pagination">
                <a href="https://example.com/directory?page=2">Next</a>
            </div>
        </body>
    </html>
    """
    page2_html = """
    <html>
        <body>
            <a href="/exhibitor/item2">Item 2</a>
        </body>
    </html>
    """

    profile = SiteProfile(
        url="https://example.com/exhibitors",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/exhibitors",
        final_url="https://example.com/exhibitors",
        status_code=200,
        rendered_html=rendered_dom,
        requests_made=1,
    )

    def mock_fetch_pages(url, parameter, start_page=2, budget=None):
        yield {"page": 2, "url": f"{url}?{parameter}=2", "content": page2_html}

    with patch("app.agents.universal_scraper.discover_site", return_value=profile), \
         patch("app.agents.universal_scraper.fetch_pages", side_effect=mock_fetch_pages):

        result = scrape("https://example.com/exhibitors", browser_client=mock_client)

    assert result.success is True
    assert result.strategy == "BROWSER_HTML"
    assert len(result.records) == 2
    assert result.pages_scraped == 2
    names = [r.company_name for r in result.records]
    assert "Item 1" in names
    assert "Item 2" in names


def test_max_records_enforced_in_browser_strategy(monkeypatch):
    """max_records budget stops collection even if more records are found in rendered pagination."""
    rendered_dom = """
    <html>
        <body>
            <a href="/exhibitor/item1">Item 1</a>
            <a href="/exhibitor/item2">Item 2</a>
            <a href="https://example.com/directory?page=2">Next</a>
        </body>
    </html>
    """
    page2_html = """
    <html>
        <body>
            <a href="/exhibitor/item3">Item 3</a>
            <a href="/exhibitor/item4">Item 4</a>
        </body>
    </html>
    """

    profile = SiteProfile(
        url="https://example.com/exhibitors",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/exhibitors",
        final_url="https://example.com/exhibitors",
        status_code=200,
        rendered_html=rendered_dom,
        requests_made=1,
    )

    def mock_fetch_pages(url, parameter, start_page=2, budget=None):
        yield {"page": 2, "url": f"{url}?{parameter}=2", "content": page2_html}

    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_records=3),
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile), \
         patch("app.agents.universal_scraper.fetch_pages", side_effect=mock_fetch_pages):

        result = scrape("https://example.com/exhibitors", browser_client=mock_client)

    assert len(result.records) == 3


def test_max_pages_enforced_in_browser_strategy(monkeypatch):
    """max_pages budget prevents scraping additional rendered pages."""
    rendered_dom = """
    <html>
        <body>
            <a href="/exhibitor/item1">Item 1</a>
            <a href="https://example.com/directory?page=2">Next</a>
        </body>
    </html>
    """
    profile = SiteProfile(
        url="https://example.com/exhibitors",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/exhibitors",
        final_url="https://example.com/exhibitors",
        status_code=200,
        rendered_html=rendered_dom,
        requests_made=1,
    )

    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_pages=1),
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/exhibitors", browser_client=mock_client)

    assert result.pages_scraped == 1
    assert len(result.records) == 1


# ==============================================================================
# 7. Metadata and Error Handling
# ==============================================================================

def test_strategy_and_urls_recorded_accurately():
    """result.strategy is BROWSER_HTML, source_url is preserved, and final_url is updated."""
    profile = SiteProfile(
        url="https://example.com/start",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/start",
        final_url="https://example.com/redirected-directory",
        status_code=200,
        rendered_html='<a href="/company/test">Test Co</a>',
        requests_made=2,
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/start", browser_client=mock_client)

    assert result.strategy == "BROWSER_HTML"
    assert result.source_url == "https://example.com/start"
    assert result.final_url == "https://example.com/redirected-directory"
    assert result.requests_made >= 2


def test_browser_failure_handled_gracefully():
    """When browser rendering raises an unhandled error, it is recorded in errors."""
    profile = SiteProfile(
        url="https://example.com/fail",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.side_effect = RuntimeError("Playwright unexpected crash")

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/fail", browser_client=mock_client)

    assert result.success is False
    assert any("Playwright unexpected crash" in err for err in result.errors)


def test_browser_rendered_iframe_bounded_depth(monkeypatch):
    """Rendered DOM containing iframe extracts records with bounded depth."""
    rendered_dom = """
    <html>
        <body>
            <iframe src="https://example.com/iframe_dir"></iframe>
        </body>
    </html>
    """
    iframe_html = """
    <html>
        <body>
            <a href="/exhibitor/framed">Framed Corp</a>
        </body>
    </html>
    """

    profile = SiteProfile(
        url="https://example.com/parent",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/parent",
        final_url="https://example.com/parent",
        status_code=200,
        rendered_html=rendered_dom,
        requests_made=1,
    )

    def mock_fetch(url, *args, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": iframe_html,
        }

    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch)

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/parent", browser_client=mock_client)

    assert result.success is True
    assert result.strategy == "BROWSER_HTML"
    assert len(result.records) == 1
    assert result.records[0].company_name == "Framed Corp"
