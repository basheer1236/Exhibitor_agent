import pytest
from app.agents.universal_scraper import scrape
from app.models.scrape_budget import ScrapeBudget
from app.models.site_profile import SiteProfile

class MockResponse:
    def __init__(self, json_data=None, ok=True, status_code=200):
        self._json_data = json_data
        self.ok = ok
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._json_data is None:
            raise ValueError("Invalid JSON")
        return self._json_data


def test_api_direct_list(monkeypatch):
    fetch_count = {"pages": 0}
    
    def mock_requests_get(url, *args, **kwargs):
        fetch_count["pages"] += 1
        if "page=2" in url:
            return MockResponse([])
        return MockResponse([
            {"name": "Company A"},
            {"name": "Company B"}
        ])

    monkeypatch.setattr("requests.get", mock_requests_get)

    # Mock discover_site to return an API profile
    def mock_discover_site(url):
        return SiteProfile(
            url="https://api.example.com/exhibitors",
            is_json=True,
            initial_content='[{"name": "Company A"}, {"name": "Company B"}]'
        )
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    result = scrape("https://api.example.com/exhibitors")
    
    assert result.success is True
    assert result.strategy == "API"
    assert len(result.records) == 2
    assert result.records[0].company_name == "Company A"
    assert result.pages_scraped == 1 # stopped at page 2 which is empty
    # Discovery does 0 requests because we mocked discover_site. 
    # But wait, universal_scraper does not count discovery if discover_site is mocked.
    # It adds 1 for the initial extraction. Then 1 for page=2. Total 2.
    assert result.requests_made == 2


def test_api_metadata_and_multiple_pages(monkeypatch):
    fetch_count = {"requests": 0}
    
    def mock_requests_get(url, *args, **kwargs):
        fetch_count["requests"] += 1
        # Page 0
        if "page=0" in url or "?" not in url:
            return MockResponse({
                "metaData": {"page": 0, "totalPages": 3},
                "data": [{"name": "Comp 1"}]
            })
        elif "page=1" in url:
            return MockResponse({
                "metaData": {"page": 1, "totalPages": 3},
                "data": [{"name": "Comp 2"}]
            })
        elif "page=2" in url:
            return MockResponse({
                "metaData": {"page": 2, "totalPages": 3},
                "data": [{"name": "Comp 3"}]
            })
        else:
            return MockResponse({"data": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url=url,
            is_json=True,
            initial_content='{"metaData": {"page": 0, "totalPages": 3}, "data": [{"name": "Comp 1"}]}'
        )
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    result = scrape("https://api.example.com/exhibitors")
    
    assert result.success is True
    assert result.strategy == "API"
    assert len(result.records) == 3
    assert result.pages_scraped == 3
    # 1 for initial + page=1 + page=2 = 3 requests made
    assert result.requests_made == 3


def test_api_candidate_selection(monkeypatch):
    from app.models.site_profile import ApiCandidate
    fetch_count = {"requests": 0}
    
    def mock_requests_get(url, *args, **kwargs):
        fetch_count["requests"] += 1
        if url == "https://example.com/api/bad":
            return MockResponse({"error": "not found"}, ok=False, status_code=404)
        if url == "https://example.com/api/empty":
            return MockResponse({"status": "ok"}) # no collection
        if url == "https://example.com/api/good":
            return MockResponse({"results": [{"title": "Candidate Co"}]})
        if url == "https://example.com/api/good?page=2":
            return MockResponse({"results": []})
        return MockResponse({"results": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        profile = SiteProfile(url=url, is_html=True)
        profile.has_api_candidates = True
        profile.api_candidates = [
            ApiCandidate(url="https://example.com/api/bad"),
            ApiCandidate(url="https://example.com/api/empty"),
            ApiCandidate(url="https://example.com/api/good"),
        ]
        return profile
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    result = scrape("https://example.com")
    
    assert result.success is True
    assert result.strategy == "API"
    assert len(result.records) == 1
    assert result.records[0].company_name == "Candidate Co"
    
    # Total requests:
    # 1 for discovery + 3 for candidate verifications (bad, empty, good) + 1 for pagination page 2 = 5
    assert result.requests_made == 5


def test_api_budgets(monkeypatch):
    fetch_count = {"requests": 0}
    
    def mock_requests_get(url, *args, **kwargs):
        fetch_count["requests"] += 1
        return MockResponse({"items": [{"name": f"Comp {fetch_count['requests']}"}]})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url=url,
            is_json=True,
            initial_content='{"items": [{"name": "Initial"}]}'
        )
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    # Test max_records
    monkeypatch.setattr("app.agents.universal_scraper.ScrapeBudget", lambda: ScrapeBudget(max_records=2))
    result = scrape("https://api.example.com/exhibitors")
    assert len(result.records) == 2
    
    # Test max_pages
    monkeypatch.setattr("app.agents.universal_scraper.ScrapeBudget", lambda: ScrapeBudget(max_pages=2, max_records=100))
    result2 = scrape("https://api.example.com/exhibitors")
    # Initial page + 1 fetched page = 2 pages total
    assert result2.pages_scraped == 2


def test_api_invalid_json_termination(monkeypatch):
    fetch_count = {"requests": 0}
    
    def mock_requests_get(url, *args, **kwargs):
        fetch_count["requests"] += 1
        return MockResponse(None) # Invalid JSON

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url=url,
            is_json=True,
            initial_content='[{"name": "Initial"}]'
        )
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    result = scrape("https://api.example.com/exhibitors")
    
    assert len(result.records) == 1
    assert result.pages_scraped == 1
    # Only 1 additional request made, which failed JSON parsing and broke the loop
    assert result.requests_made == 2


def test_api_duplicate_records_termination(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockResponse([{"name": "Initial"}]) # Always returns the same

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url=url,
            is_json=True,
            initial_content='[{"name": "Initial"}]'
        )
    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)
    
    result = scrape("https://api.example.com/exhibitors")
    
    assert len(result.records) == 1
    assert result.pages_scraped == 1 # Loop detects dupes and breaks, doesn't increment pages_scraped for the duplicate page


def test_candidate_verification_request_counting_and_budget(monkeypatch):
    from app.models.site_profile import ApiCandidate
    requested_urls = []

    def mock_requests_get(url, *args, **kwargs):
        requested_urls.append(url)
        if url == "https://example.com/api/cand1":
            return MockResponse({"status": "no records"})
        if url == "https://example.com/api/cand2":
            return MockResponse({"status": "no records"})
        if url == "https://example.com/api/cand3":
            return MockResponse({"results": [{"name": "Cand3 Co"}]})
        return MockResponse({"results": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        profile = SiteProfile(url=url, is_html=True)
        profile.has_api_candidates = True
        profile.api_candidates = [
            ApiCandidate(url="https://example.com/api/cand1"),
            ApiCandidate(url="https://example.com/api/cand2"),
            ApiCandidate(url="https://example.com/api/cand3"),
        ]
        return profile

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)

    # Budget has max_requests = 2 (1 for discovery + 1 candidate verification)
    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_requests=2)
    )

    result = scrape("https://example.com")

    # Discovery used 1 request.
    # cand1 was verified (1 request).
    # Total requests reached max_requests=2.
    # cand2 and cand3 were NOT requested!
    assert result.requests_made == 2
    assert requested_urls == ["https://example.com/api/cand1"]
    assert result.success is False


def test_no_blind_parameter_cycling_and_deterministic_selection(monkeypatch):
    from urllib.parse import urlparse, parse_qs
    requested_params = []

    def mock_requests_get(url, *args, **kwargs):
        parsed = urlparse(url)
        params = list(parse_qs(parsed.query).keys())
        requested_params.extend(params)
        if "pageNumber=2" in url:
            return MockResponse({"results": [{"name": "Page 2 Co"}]})
        return MockResponse({"results": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url="https://example.com/api/items?pageNumber=1",
            is_json=True,
            initial_content='{"results": [{"name": "Page 1 Co"}]}'
        )

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)

    result = scrape("https://example.com/api/items?pageNumber=1")

    assert result.success is True
    assert len(result.records) == 2
    # Scraper must select "pageNumber" deterministically and NOT try "page", "pageIndex", "offset", "start"
    assert "pageNumber" in requested_params
    assert "page" not in requested_params
    assert "pageIndex" not in requested_params
    assert "offset" not in requested_params
    assert "start" not in requested_params


def test_zero_based_pagination(monkeypatch):
    pages_fetched = []

    def mock_requests_get(url, *args, **kwargs):
        if "page=1" in url:
            pages_fetched.append(1)
            return MockResponse({
                "metaData": {"page": 1, "totalPages": 2},
                "data": [{"name": "Page 1 Co"}]
            })
        return MockResponse({"data": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url="https://example.com/api/items",
            is_json=True,
            initial_content='{"metaData": {"page": 0, "totalPages": 2}, "data": [{"name": "Page 0 Co"}]}'
        )

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)

    result = scrape("https://example.com/api/items")

    assert result.success is True
    assert len(result.records) == 2
    # Because metaData had page: 0, next page requested must be page=1 (not page=2)
    assert pages_fetched == [1]
    assert result.pages_scraped == 2


def test_offset_pagination_step_derivation(monkeypatch):
    offsets_fetched = []

    def mock_requests_get(url, *args, **kwargs):
        if "offset=25" in url:
            offsets_fetched.append(25)
            return MockResponse({
                "meta": {"limit": 25},
                "items": [{"name": "Item 26"}]
            })
        return MockResponse({"items": []})

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover_site(url):
        return SiteProfile(
            url="https://example.com/api/items?offset=0",
            is_json=True,
            initial_content='{"meta": {"limit": 25}, "items": [{"name": "Item 1"}]}'
        )

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover_site)

    result = scrape("https://example.com/api/items?offset=0")

    assert result.success is True
    # Initial was offset 0. Limit is 25. Next fetch must be offset=25 (step = 25 derived from limit)
    assert offsets_fetched == [25]
    assert len(result.records) == 2
