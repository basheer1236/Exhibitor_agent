import pytest
from app.agents.universal_scraper import scrape
from app.models.scrape_budget import ScrapeBudget

HTML_PAGE_1 = """
<html><body>
<a href="/exhibitor/a">Company A</a>
<a href="/exhibitor/b">Company B</a>
<a href="/list?page=2">Next Page</a>
</body></html>
"""

HTML_PAGE_2 = """
<html><body>
<a href="/exhibitor/c">Company C</a>
<a href="/exhibitor/d">Company D</a>
<a href="/list?page=3">Next Page</a>
</body></html>
"""

HTML_PAGE_3 = """
<html><body>
<a href="/exhibitor/e">Company E</a>
<a href="/exhibitor/a">Company A</a> <!-- Duplicate -->
<a href="/list?page=4">Next Page</a>
</body></html>
"""

HTML_PAGE_EMPTY = """
<html><body>
<!-- No records -->
</body></html>
"""

class MockResponse:
    def __init__(self, text):
        self.text = text
        self.ok = True
        self.status_code = 200

def setup_mocks(monkeypatch, fetch_count):
    def mock_fetch_url(url):
        fetch_count["root"] += 1
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": HTML_PAGE_1,
        }

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)

    def mock_requests_get(url, *args, **kwargs):
        fetch_count["pages"] += 1
        if "page=2" in url:
            return MockResponse(HTML_PAGE_2)
        elif "page=3" in url:
            return MockResponse(HTML_PAGE_3)
        else:
            return MockResponse(HTML_PAGE_EMPTY)
            
    monkeypatch.setattr("app.extraction.pagination_executor.requests.get", mock_requests_get)


def test_pagination_multiple_pages_and_dedup(monkeypatch):
    fetch_count = {"root": 0, "pages": 0}
    setup_mocks(monkeypatch, fetch_count)

    result = scrape("https://example.com/exhibitors")
    
    assert result.success is True
    assert result.strategy == "HTML_DETAIL"
    
    # Total unique: A, B, C, D, E = 5
    assert len(result.records) == 5
    
    # Root request should only happen ONCE
    assert fetch_count["root"] == 1
    
    # Pagination should fetch page 2, 3, and 4 (empty, which causes break)
    assert fetch_count["pages"] == 3
    
    # Result tracks pages processed that had records
    assert result.pages_scraped == 3
    assert result.requests_made == 3


def test_pagination_max_records_budget(monkeypatch):
    fetch_count = {"root": 0, "pages": 0}
    setup_mocks(monkeypatch, fetch_count)
    
    # Patch ScrapeBudget inside universal_scraper
    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget", 
        lambda: ScrapeBudget(max_records=3)
    )

    result = scrape("https://example.com/exhibitors")
    
    # Should stop after extracting 3 records (Page 1 has 2, Page 2 yields 1 more to hit budget)
    assert len(result.records) == 3
    assert fetch_count["root"] == 1
    assert fetch_count["pages"] == 1 # Only needs to fetch page 2 to hit budget
    
    assert result.pages_scraped == 2


def test_pagination_max_pages_budget(monkeypatch):
    fetch_count = {"root": 0, "pages": 0}
    setup_mocks(monkeypatch, fetch_count)
    
    # Patch ScrapeBudget inside universal_scraper
    # max_pages=1 for fetch_pages loop means it only checks page 2 (start_page=2 -> range(2, 3))
    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget", 
        lambda: ScrapeBudget(max_pages=1)
    )

    result = scrape("https://example.com/exhibitors")
    
    # Page 1 (2 records) + Page 2 (2 records) = 4 total
    assert len(result.records) == 4
    assert fetch_count["root"] == 1
    assert fetch_count["pages"] == 1 # Only fetches page 2
    
    assert result.pages_scraped == 2


def test_root_page_only(monkeypatch):
    fetch_count = {"root": 0, "pages": 0}
    
    def mock_fetch_url(url):
        fetch_count["root"] += 1
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": "<html><body><a href='/exhibitor/a'>Company A</a></body></html>",
        }

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)

    result = scrape("https://example.com/exhibitors")
    
    assert len(result.records) == 1
    assert fetch_count["root"] == 1
    assert fetch_count["pages"] == 0
    assert result.pages_scraped == 1
