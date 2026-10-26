import pytest
from app.agents.universal_scraper import scrape
from app.models.site_profile import SiteProfile
from app.models.scrape_budget import ScrapeBudget
from tests.test_http_security import MockHTTPResponse


def test_iframe_html_extraction(monkeypatch):
    """Test deterministic extraction when an iframe contains HTML exhibitor directory."""
    def mock_fetch_url(url, *args, **kwargs):
        if url == "https://example.com/main":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": """
                <html>
                    <body>
                        <iframe src="https://example.com/embedded_directory"></iframe>
                    </body>
                </html>
                """,
            }
        elif url == "https://example.com/embedded_directory":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": """
                <html>
                    <body>
                        <a href="/exhibitor/acme">ACME Corp</a>
                        <a href="/exhibitor/beta">Beta Corp</a>
                    </body>
                </html>
                """,
            }
        return {"url": url, "status_code": 404, "headers": {}, "content": ""}

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)

    result = scrape("https://example.com/main")

    assert result.success is True
    assert result.strategy == "IFRAME"
    assert len(result.records) == 2
    assert result.records[0].company_name == "ACME Corp"
    assert result.records[1].company_name == "Beta Corp"
    # 1 for discovery + 1 for iframe fetch = 2 requests
    assert result.requests_made == 2
    assert result.pages_scraped == 1


def test_iframe_api_json_extraction(monkeypatch):
    """Test deterministic extraction when an iframe src serves JSON."""
    def mock_fetch_url(url, *args, **kwargs):
        if url == "https://example.com/main":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": """
                <html>
                    <body>
                        <iframe src="https://example.com/embedded_api"></iframe>
                    </body>
                </html>
                """,
            }
        elif url == "https://example.com/embedded_api":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "content": '[{"name": "API Exhibitor 1"}, {"name": "API Exhibitor 2"}]',
            }
        return {"url": url, "status_code": 404, "headers": {}, "content": ""}

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)

    result = scrape("https://example.com/main")

    assert result.success is True
    assert result.strategy == "IFRAME"
    assert len(result.records) == 2
    assert result.records[0].company_name == "API Exhibitor 1"
    assert result.records[1].company_name == "API Exhibitor 2"


def test_iframe_html_with_pagination(monkeypatch):
    """Test pagination inside an iframe HTML document."""
    def mock_fetch_url(url, *args, **kwargs):
        if url == "https://example.com/main":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": '<iframe src="https://example.com/iframe_list"></iframe>',
            }
        elif url == "https://example.com/iframe_list":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": """
                <html>
                    <body>
                        <a href="/exhibitor/1">Company One</a>
                        <a href="https://example.com/iframe_list?page=2">Next Page</a>
                    </body>
                </html>
                """,
            }
        return {"url": url, "status_code": 404, "headers": {}, "content": ""}

    def mock_requests_get(url, *args, **kwargs):
        if "page=2" in url:
            return MockHTTPResponse(
                status_code=200,
                text="<html><body><a href='/exhibitor/2'>Company Two</a></body></html>",
                url=url,
            )
        return MockHTTPResponse(status_code=404, text="", url=url)

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.extraction.pagination_executor.requests.get", mock_requests_get)

    result = scrape("https://example.com/main")

    assert result.success is True
    assert result.strategy == "IFRAME"
    assert len(result.records) == 2
    assert result.pages_scraped == 2
    # 1 for main page + 1 for iframe page 1 + 1 for iframe page 2 = 3 requests
    assert result.requests_made == 3


def test_iframe_ssrf_protection(monkeypatch):
    """Test that an iframe pointing to a private/unsafe IP is blocked safely."""
    def mock_fetch_url(url, *args, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": '<iframe src="http://127.0.0.1:8000/internal_directory"></iframe>',
        }

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)

    result = scrape("https://example.com/main")

    assert result.strategy == "IFRAME"
    assert result.success is False
    assert len(result.records) == 0
    # Error logged for blocked iframe
    assert any("Failed to fetch iframe" in err or "Blocked" in err for err in result.errors)


def test_iframe_budget_max_records(monkeypatch):
    """Test that max_records budget is enforced in iframe extraction."""
    def mock_fetch_url(url, *args, **kwargs):
        if url == "https://example.com/main":
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": '<iframe src="https://example.com/iframe_items"></iframe>',
            }
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "content": '[{"name": "Co 1"}, {"name": "Co 2"}, {"name": "Co 3"}]',
        }

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)
    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_records=2)
    )

    result = scrape("https://example.com/main")

    assert result.success is True
    assert len(result.records) == 2
