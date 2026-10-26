import pytest
from unittest.mock import MagicMock, patch

from app.agents.universal_scraper import scrape
from app.models.exhibitor import ExhibitorRecord
from app.models.extraction_quality import ExtractionQuality, QualityStatus
from app.models.scrape_budget import ScrapeBudget
from app.models.site_profile import SiteProfile, ApiCandidate
from app.quality.validator import validate_record
from app.quality.evaluator import evaluate_extraction_quality
from app.extraction.normalizer import (
    normalize_string,
    normalize_company_name,
    normalize_url,
    normalize_email,
    normalize_record,
)
from app.extraction.deduplicator import deduplicate_with_stats, deduplicate_records
from app.discovery.api import evaluate_api_payload, rank_api_candidates
from app.browser import BrowserClient, BrowserRenderResult


# ==============================================================================
# 1. Quality Model
# ==============================================================================

def test_quality_model():
    """Verify ExtractionQuality model fields, defaults, and types."""
    q = ExtractionQuality(
        records_extracted=10,
        valid_records=9,
        invalid_records=1,
        duplicates_removed=2,
        company_name_coverage=1.0,
        detail_url_coverage=0.9,
        website_coverage=0.8,
        email_coverage=0.5,
        pagination_completeness=1.0,
        quality_score=0.92,
        quality_status=QualityStatus.COMPLETE,
        status_reason="All records valid",
        validation_issues=["Minor issue"],
    )
    assert q.records_extracted == 10
    assert q.valid_records == 9
    assert q.invalid_records == 1
    assert q.quality_status == QualityStatus.COMPLETE
    assert q.quality_score == 0.92
    assert q.validation_issues == ["Minor issue"]


# ==============================================================================
# 2-5. Quality Statuses: COMPLETE, PARTIAL, LOW_QUALITY, FAILED
# ==============================================================================

def test_complete_result():
    """Extraction completing with valid records and no budget cutoffs is COMPLETE."""
    records = [
        ExhibitorRecord(
            company_name="Acme Technologies",
            detail_url="https://example.com/ex/1",
            website="https://acme.com",
            source_url="https://example.com",
        ),
        ExhibitorRecord(
            company_name="Beta Solutions",
            detail_url="https://example.com/ex/2",
            website="https://beta.com",
            source_url="https://example.com",
        ),
    ]
    quality = evaluate_extraction_quality(
        records=records,
        pages_scraped=1,
        expected_pages=1,
        budget_limited=False,
    )
    assert quality.quality_status == QualityStatus.COMPLETE
    assert quality.quality_score >= 0.70
    assert quality.valid_records == 2
    assert quality.invalid_records == 0


def test_partial_result():
    """Extraction truncated early due to budget limits is PARTIAL."""
    records = [
        ExhibitorRecord(
            company_name="Acme Corp",
            detail_url="https://example.com/ex/1",
            source_url="https://example.com",
        )
    ]
    quality = evaluate_extraction_quality(
        records=records,
        pages_scraped=1,
        expected_pages=5,
        budget_limited=True,
    )
    assert quality.quality_status == QualityStatus.PARTIAL
    assert quality.pagination_completeness < 1.0
    assert "budget" in quality.status_reason.lower() or "expected" in quality.status_reason.lower()


def test_low_quality_result():
    """Extraction with malformed or missing key fields is LOW_QUALITY."""
    records = [
        ExhibitorRecord(
            company_name="U",  # Too short
            detail_url="javascript:void(0)",  # Malformed URL
            source_url="https://example.com",
        ),
        ExhibitorRecord(
            company_name="Unknown",  # Generic placeholder
            source_url="https://example.com",
        ),
    ]
    quality = evaluate_extraction_quality(
        records=records,
        pages_scraped=1,
    )
    assert quality.quality_status == QualityStatus.LOW_QUALITY
    assert quality.quality_score < 0.50
    assert len(quality.validation_issues) > 0


def test_failed_result():
    """Empty extraction or total failure produces FAILED status."""
    quality = evaluate_extraction_quality(
        records=[],
        scrape_error="HTTP 404 Not Found",
    )
    assert quality.quality_status == QualityStatus.FAILED
    assert quality.quality_score == 0.0
    assert quality.valid_records == 0


# ==============================================================================
# 6-7. Record Validation: Invalid Name & Malformed URLs
# ==============================================================================

def test_invalid_company_name():
    """Structural validation flags empty, whitespace, short, or generic company names."""
    rec_empty = ExhibitorRecord(company_name="", source_url="https://example.com")
    is_valid, issues = validate_record(rec_empty)
    assert is_valid is False
    assert any("empty" in i.lower() for i in issues)

    rec_placeholder = ExhibitorRecord(company_name="Unknown", source_url="https://example.com")
    is_valid, issues = validate_record(rec_placeholder)
    assert is_valid is False
    assert any("generic" in i.lower() for i in issues)

    rec_valid = ExhibitorRecord(company_name="ABC Pvt. Ltd.", source_url="https://example.com")
    is_valid, _ = validate_record(rec_valid)
    assert is_valid is True


def test_malformed_detail_url():
    """Structural validation flags invalid detail URLs, website URLs, and email addresses."""
    rec = ExhibitorRecord(
        company_name="Acme Corp",
        detail_url="not-a-valid-url",
        website="ftp://invalid-scheme",
        email="invalid-email-at-domain",
        source_url="https://example.com",
    )
    is_valid, issues = validate_record(rec)
    assert is_valid is False
    assert any("detail_url" in i.lower() for i in issues)
    assert any("website" in i.lower() for i in issues)
    assert any("email" in i.lower() for i in issues)


# ==============================================================================
# 8-11. Deduplication Priority
# ==============================================================================

def test_duplicate_removal_count():
    """deduplicate_with_stats returns exact duplicates_removed count."""
    records = [
        ExhibitorRecord(company_name="Alpha Co", detail_url="https://example.com/a", source_url="https://example.com"),
        ExhibitorRecord(company_name="Alpha Co", detail_url="https://example.com/a", source_url="https://example.com?p=2"),
        ExhibitorRecord(company_name="Beta Co", detail_url="https://example.com/b", source_url="https://example.com"),
    ]
    unique, dups = deduplicate_with_stats(records)
    assert len(unique) == 2
    assert dups == 1


def test_exhibitor_id_deduplication():
    """Priority 1: exhibitor_id deduplicates even if names differ slightly."""
    records = [
        ExhibitorRecord(exhibitor_id="EX-101", company_name="Acme Inc", source_url="https://example.com"),
        ExhibitorRecord(exhibitor_id="EX-101", company_name="Acme Corporation", source_url="https://example.com"),
    ]
    unique, dups = deduplicate_with_stats(records)
    assert len(unique) == 1
    assert dups == 1


def test_detail_url_deduplication():
    """Priority 2: normalized detail_url deduplicates records."""
    records = [
        ExhibitorRecord(company_name="Gamma", detail_url="https://example.com/exhibitors/gamma/", source_url="https://example.com"),
        ExhibitorRecord(company_name="Gamma Inc", detail_url="https://example.com/exhibitors/gamma", source_url="https://example.com"),
    ]
    unique, dups = deduplicate_with_stats(records)
    assert len(unique) == 1
    assert dups == 1


def test_website_and_company_deduplication():
    """Priority 3: normalized website + company_name deduplicates records."""
    records = [
        ExhibitorRecord(company_name="Delta Systems", website="https://delta.com/", source_url="https://example.com/1"),
        ExhibitorRecord(company_name="delta systems", website="https://delta.com", source_url="https://example.com/2"),
    ]
    unique, dups = deduplicate_with_stats(records)
    assert len(unique) == 1
    assert dups == 1


# ==============================================================================
# 12-15. API Candidate Evaluation & Ranking
# ==============================================================================

def test_unrelated_json_api_candidate_rejected():
    """Candidates returning analytics, configs, or empty collections are rejected."""
    analytics_payload = {
        "analytics": [
            {"tracker": "GA4", "metric": 100},
            {"tracker": "Mixpanel", "metric": 200},
        ]
    }
    eval_analytics = evaluate_api_payload(analytics_payload, "https://example.com/api/analytics")
    assert eval_analytics["valid_exhibitor_api"] is False

    config_payload = {
        "config": [
            {"theme": "dark", "debug": False},
        ]
    }
    eval_config = evaluate_api_payload(config_payload, "https://example.com/api/config")
    assert eval_config["valid_exhibitor_api"] is False

    empty_payload = {"items": []}
    eval_empty = evaluate_api_payload(empty_payload, "https://example.com/api/empty")
    assert eval_empty["valid_exhibitor_api"] is False


def test_valid_exhibitor_api_candidate_selected():
    """Valid exhibitor API payload is correctly evaluated as True."""
    exhibitor_payload = {
        "exhibitors": [
            {"name": "SolarTech Global", "booth": "A-12", "website": "https://solartech.com"},
            {"name": "WindPower Corp", "booth": "B-34", "website": "https://windpower.com"},
        ],
        "total": 2,
    }
    eval_exhibitor = evaluate_api_payload(exhibitor_payload, "https://example.com/api/exhibitors")
    assert eval_exhibitor["valid_exhibitor_api"] is True
    assert eval_exhibitor["useful_records"] == 2
    assert eval_exhibitor["score"] > 20.0


def test_deterministic_api_candidate_ranking():
    """API candidate ranking deterministically orders richer candidates above sparser ones."""
    richer_payload = {
        "results": [
            {"name": f"Exhibitor {i}", "website": f"https://ex{i}.com", "detail_url": f"/ex/{i}"}
            for i in range(5)
        ],
        "total_pages": 3,
    }
    sparse_payload = {
        "results": [
            {"name": "Only One"}
        ]
    }
    candidates = [
        ("https://example.com/api/sparse", sparse_payload),
        ("https://example.com/api/richer", richer_payload),
    ]
    ranked = rank_api_candidates(candidates)
    assert ranked[0]["url"] == "https://example.com/api/richer"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_multiple_api_candidates_in_scraper(monkeypatch):
    """End-to-end scraper picks the valid exhibitor API candidate over unrelated analytics."""
    def mock_fetch(url, *args, **kwargs):
        if "analytics" in url:
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "content": '{"analytics": [{"metric": 42}]}',
            }
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "application/json"},
            "content": '{"exhibitors": [{"name": "Pioneer Co", "booth": "100"}]}',
        }

    monkeypatch.setattr("app.discovery.http.fetch_url", mock_fetch)
    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch)
    monkeypatch.setattr("app.discovery.api.fetch_url", mock_fetch)

    def mock_discover(url):
        p = SiteProfile(url=url, is_html=True)
        p.has_api_candidates = True
        p.api_candidates = [
            ApiCandidate(url="https://example.com/api/analytics"),
            ApiCandidate(url="https://example.com/api/directory"),
        ]
        return p

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover)

    result = scrape("https://example.com")
    assert result.success is True
    assert result.strategy == "API"
    assert len(result.records) == 1
    assert result.records[0].company_name == "Pioneer Co"


# ==============================================================================
# 16-17. Pagination Completeness & Partial Classification
# ==============================================================================

def test_pagination_completeness():
    """Pagination completeness is accurately computed when expected pages are known."""
    quality = evaluate_extraction_quality(
        records=[ExhibitorRecord(company_name="Acme Corporation", source_url="https://example.com")],
        pages_scraped=3,
        expected_pages=10,
    )
    assert quality.pagination_completeness == 0.3
    assert quality.quality_status == QualityStatus.PARTIAL


def test_budget_limited_pagination_marked_partial(monkeypatch):
    """When scrape budget terminates pagination early, quality_status is PARTIAL."""
    fetch_count = {"pages": 0}

    def mock_requests_get(url, *args, **kwargs):
        fetch_count["pages"] += 1
        page_num = fetch_count["pages"]
        return type("Resp", (), {
            "ok": True,
            "status_code": 200,
            "text": f'<a href="/exhibitor/item{page_num}">Company {page_num}</a>'
                    f'<a href="https://example.com/exhibitors?page={page_num+1}">Next</a>',
            "json": lambda self: {"items": [{"name": f"Company {page_num}"}], "total_pages": 10},
        })()

    monkeypatch.setattr("requests.get", mock_requests_get)

    def mock_discover(url):
        p = SiteProfile(url=url, is_html=True)
        p.initial_content = '<a href="/exhibitor/item1">Company 1</a><a href="https://example.com/exhibitors?page=2">Next</a>'
        p.has_detail_links = True
        p.detail_links = ["https://example.com/exhibitor/item1"]
        p.has_pagination = True
        p.pagination_signals = [{"parameter": "page"}]
        return p

    monkeypatch.setattr("app.agents.universal_scraper.discover_site", mock_discover)
    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_pages=2),
    )

    result = scrape("https://example.com/exhibitors")
    assert result.success is True
    assert result.quality_status == "PARTIAL"
    assert result.quality.quality_status == QualityStatus.PARTIAL


# ==============================================================================
# 18-19. Normalization & URL Normalization
# ==============================================================================

def test_normalization():
    """Unicode whitespace and HTML entities are cleaned while meaningful punctuation is preserved."""
    raw_name = "   ABC &amp; Sons Pvt. Ltd.\u00a0\u200b  "
    normalized = normalize_company_name(raw_name)
    assert normalized == "ABC & Sons Pvt. Ltd."

    # Normalized record preserves punctuation
    raw_record = ExhibitorRecord(
        company_name="   XYZ Corp.   ",
        email="  INFO@XYZ.COM  ",
        phone="  +1 (555) 0199  ",
        source_url="https://example.com",
    )
    norm = normalize_record(raw_record)
    assert norm.company_name == "XYZ Corp."
    assert norm.email == "info@xyz.com"
    assert norm.phone == "+1 (555) 0199"


def test_url_normalization():
    """URL normalization strips fragments, trailing path slashes, and resolves relatives while keeping queries."""
    # Fragment stripped, query preserved
    u1 = normalize_url("https://example.com/directory/?page=2#section")
    assert u1 == "https://example.com/directory?page=2"

    # Relative URL resolved against base
    u2 = normalize_url("/company/123", base_url="https://example.com/portal/")
    assert u2 == "https://example.com/company/123"

    # Query with ID preserved
    u3 = normalize_url("https://example.com/profile?id=999&tab=overview")
    assert u3 == "https://example.com/profile?id=999&tab=overview"


# ==============================================================================
# 20-22. Observability & Metadata
# ==============================================================================

def test_scrape_result_metadata():
    """ScrapeResult captures quality, duration, deduplication count, and browser metadata."""
    result = scrape("https://example.com/exhibitors")
    assert hasattr(result, "quality_status")
    assert hasattr(result, "quality_score")
    assert hasattr(result, "duplicates_removed")
    assert hasattr(result, "browser_used")
    assert hasattr(result, "fallback_reason")
    assert hasattr(result, "duration_seconds")
    assert result.duration_seconds >= 0.0


def test_browser_fallback_metadata(monkeypatch):
    """When browser rendering executes, browser_used is True and fallback_reason is recorded."""
    profile = SiteProfile(
        url="https://example.com/spa",
        is_html=True,
        initial_content='<div id="root"></div><script src="app.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/spa",
        final_url="https://example.com/spa",
        status_code=200,
        rendered_html='<a href="/exhibitor/spaco">SPA Co</a>',
        requests_made=1,
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/spa", browser_client=mock_client)

    assert result.browser_used is True
    assert result.fallback_reason is not None
    assert "JavaScript" in result.fallback_reason or "JS evidence" in result.fallback_reason
    assert result.strategy == "BROWSER_HTML"


def test_execution_duration():
    """Duration in seconds is positive and recorded accurately."""
    result = scrape("https://example.com")
    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0.0


# ==============================================================================
# 23-24. Regressions: Iframe and Browser
# ==============================================================================

def test_iframe_regression(monkeypatch):
    """Phase 5A iframe extraction continues working with Phase 6 quality evaluation."""
    def mock_fetch(url, *args, **kwargs):
        if "iframe" in url:
            return {
                "url": url,
                "status_code": 200,
                "headers": {"Content-Type": "text/html"},
                "content": '<a href="/exhibitor/frame-co">Frame Co</a>',
            }
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": '<iframe src="https://example.com/iframe-dir"></iframe>',
        }

    monkeypatch.setattr("app.discovery.http.fetch_url", mock_fetch)
    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch)

    result = scrape("https://example.com")
    assert result.success is True
    assert result.strategy == "IFRAME"
    assert len(result.records) == 1
    assert result.records[0].company_name == "Frame Co"
    assert result.quality_status == "COMPLETE"


def test_browser_regression(monkeypatch):
    """Phase 5B browser execution continues working and receives quality evaluation."""
    profile = SiteProfile(
        url="https://example.com/js-site",
        is_html=True,
        initial_content='<div id="app"></div><script src="bundle.js"></script>',
        javascript_required=True,
    )

    mock_client = MagicMock(spec=BrowserClient)
    mock_client.render.return_value = BrowserRenderResult(
        requested_url="https://example.com/js-site",
        final_url="https://example.com/js-site",
        status_code=200,
        rendered_html='<a href="/exhibitor/quantum">Quantum Dynamics</a>',
        requests_made=1,
    )

    with patch("app.agents.universal_scraper.discover_site", return_value=profile):
        result = scrape("https://example.com/js-site", browser_client=mock_client)

    assert result.success is True
    assert result.strategy == "BROWSER_HTML"
    assert result.records[0].company_name == "Quantum Dynamics"
    assert result.quality_status == "COMPLETE"
