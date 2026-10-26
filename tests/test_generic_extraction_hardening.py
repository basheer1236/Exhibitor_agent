from unittest.mock import patch
import pytest
from app.discovery.pagination import detect_pagination
from app.discovery.site import discover_site
from app.extraction.html_records import extract_html_records
from app.extraction.pagination_executor import fetch_pages
from app.agents.universal_scraper import scrape
from app.models.exhibitor import ExhibitorRecord
from app.models.extraction_quality import QualityStatus
from app.models.scrape_budget import ScrapeBudget
from app.models.site_profile import SiteProfile
from app.quality.evaluator import evaluate_extraction_quality


def test_website_link_not_mistaken_for_company_heading():
    """Verify that domain anchor text is not used as company name when heading exists."""
    html = """
    <article class="exhibitor-card">
        <h2 class="exhibitor-name">Acme Robotics Pvt. Ltd.</h2>
        <div class="meta">
            <span>Hall No : 4</span>
            <span>Stall No : A-102</span>
        </div>
        <div class="website">
            <a href="https://acmerobotics.com" target="_blank">acmerobotics.com</a>
        </div>
    </article>
    <article class="exhibitor-card">
        <h2 class="exhibitor-name">TRIPEAZ</h2>
        <div class="meta">
            <span>Hall No : 12</span>
            <span>Stall No : B8</span>
        </div>
        <div class="website">
            <a href="https://tripeaztaxipartners.com/" target="_blank">tripeaztaxipartners.com</a>
        </div>
    </article>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2

    r1 = records[0]
    assert r1.company_name == "Acme Robotics Pvt. Ltd."
    assert r1.hall == "4"
    assert r1.booth == "A-102"
    assert r1.website == "https://acmerobotics.com"

    r2 = records[1]
    assert r2.company_name == "TRIPEAZ"
    assert r2.hall == "12"
    assert r2.booth == "B8"
    assert r2.website == "https://tripeaztaxipartners.com"
    # Ensure domain wasn't used as company name
    assert r2.company_name != "tripeaztaxipartners.com"


def test_multiple_exhibitor_cards_on_one_page():
    """Verify multiple exhibitor cards are correctly extracted from repeated containers."""
    cards_html = "".join([
        f"""
        <div class="col-sm-6 exhibitor-item">
            <article class="card">
                <h3>Exhibitor {i}</h3>
                <p>Hall {i % 3 + 1} - Booth B{i}</p>
                <a href="https://exhibitor{i}.com">Visit Website</a>
            </article>
        </div>
        """
        for i in range(1, 6)
    ])
    html = f"<html><body><div class='grid'>{cards_html}</div></body></html>"
    records = extract_html_records(html, "https://example.com/directory")

    assert len(records) == 5
    assert records[0].company_name == "Exhibitor 1"
    assert records[0].hall == "2"
    assert records[0].booth == "B1"
    assert records[0].website == "https://exhibitor1.com"
    assert records[4].company_name == "Exhibitor 5"


def test_external_tracking_links_ignored_in_pagination():
    """Verify detect_pagination ignores external tracking/search links and non-numeric query params."""
    url = "https://example.com/exhibitors"
    links = [
        "https://www.bing.com/ck/a?!&&p=e1bcdfc7d9b64e824a8e3f668c6c50694820546e3aa613592f5a5fb5cf2f7ebb",
        "https://google.com/search?p=token123",
        "https://example.com/contact?p=inquiry",  # Non-numeric query value on same host
    ]
    result = detect_pagination(url, links)
    assert result["has_pagination"] is False
    assert len(result["signals"]) == 0


def test_internal_numeric_pagination_detected():
    """Verify internal links with numeric pagination parameter are properly detected."""
    url = "https://example.com/exhibitors"
    links = [
        "https://example.com/exhibitors?page=2",
        "https://example.com/exhibitors?page=3",
    ]
    result = detect_pagination(url, links)
    assert result["has_pagination"] is True
    assert result["signals"][0]["parameter"] == "page"


def test_repeated_page_content_halts_pagination(monkeypatch):
    """Verify fetch_pages halts when subsequent pages return identical content hashes."""
    identical_content = "<html><body><p>Same Content</p></body></html>"

    class MockResp:
        ok = True
        status_code = 200
        text = identical_content

    fetch_count = [0]

    def mock_get(url, *args, **kwargs):
        fetch_count[0] += 1
        return MockResp()

    monkeypatch.setattr("app.extraction.pagination_executor.requests.get", mock_get)

    budget = ScrapeBudget(max_pages=20)
    pages = list(fetch_pages("https://example.com/list", "page", start_page=2, budget=budget))

    # Page 2 fetched and yielded. Page 3 has identical content hash to page 2, halting fetch_pages!
    assert len(pages) == 1
    assert pages[0]["page"] == 2
    assert fetch_count[0] == 2  # page 2 (yielded) + page 3 (identical hash, broke)


def test_pagination_parameter_not_changing_records_halts_early(monkeypatch):
    """Verify scraper halts when page returns 100% duplicate records rather than looping to max_pages."""
    page_html = """
    <html><body>
    <article class="exhibitor-card">
        <h2>Static Company</h2>
        <a href="https://static.com">Website</a>
    </article>
    <article class="exhibitor-card">
        <h2>Fixed Vendor</h2>
        <a href="https://vendor.com">Website</a>
    </article>
    <a href="https://example.com/exhibitors?page=2">Next</a>
    </body></html>
    """

    class MockResp:
        ok = True
        status_code = 200
        text = page_html

    req_count = [0]

    def mock_fetch_url(url):
        return {
            "url": url,
            "status_code": 200,
            "headers": {"Content-Type": "text/html"},
            "content": page_html,
        }

    def mock_get(url, *args, **kwargs):
        req_count[0] += 1
        return MockResp()

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.agents.universal_scraper.fetch_url", mock_fetch_url)
    monkeypatch.setattr("app.extraction.pagination_executor.requests.get", mock_get)

    monkeypatch.setattr(
        "app.agents.universal_scraper.ScrapeBudget",
        lambda: ScrapeBudget(max_pages=50),
    )

    result = scrape("https://example.com/exhibitors")
    assert result.success is True
    # Should extract the 2 unique records
    assert len(result.records) == 2
    # Should NOT have made 50 requests
    assert result.pages_scraped <= 2
    assert req_count[0] <= 2


def test_hall_and_stall_extraction_variants():
    """Verify diverse real-world formatting of Hall and Stall/Booth annotations."""
    html = """
    <article>
        <h3>Alpha Ltd</h3>
        <div>Hall: 4 | Booth: 104</div>
    </article>
    <article>
        <h3>Beta Corp</h3>
        <div>Hall No : 12, Stall No : B8</div>
    </article>
    <article>
        <h3>Gamma Systems</h3>
        <div>Hall #2 - Stand 12B</div>
    </article>
    """
    records = extract_html_records(html, "https://example.com")
    assert len(records) == 3

    assert records[0].hall == "4"
    assert records[0].booth == "104"

    assert records[1].hall == "12"
    assert records[1].booth == "B8"

    assert records[2].hall == "2"
    assert records[2].booth == "12B"


def test_quality_score_suspicious_duplicate_loop():
    """Verify that a scrape producing 1 record with 99 duplicates is penalized and marked LOW_QUALITY."""
    record = ExhibitorRecord(
        company_name="tripeaztaxipartners.com",
        detail_url="https://tripeaztaxipartners.com/",
        source_url="https://example.com",
    )
    # Simulating the exact Prawaas symptom: 1 record, 99 duplicates removed, 100 pages scraped
    quality = evaluate_extraction_quality(
        records=[record],
        duplicates_removed=99,
        pages_scraped=100,
        budget_limited=True,
    )
    # Quality score must NOT be 0.96; must reflect severe duplication loop
    assert quality.quality_score < 0.50
    assert quality.quality_status == QualityStatus.LOW_QUALITY
    assert any("High duplicate contamination" in issue for issue in quality.validation_issues)
    assert any("low record yield" in issue.lower() for issue in quality.validation_issues)


def test_prawaas_representative_fixture():
    """Verify end-to-end extraction against representative Prawaas HTML markup."""
    prawaas_fixture = """
    <!DOCTYPE html>
    <html>
    <head><title>Prawaas 2026 - Exhibitors</title></head>
    <body>
        <div class="exhibitor-grid">
            <article class="exhibitor-card" id="exhibitor-0">
                <div class="exhibitor-logo-wrap">
                    <img class="exhibitor-logo" src="https://prawaas.com/storage/logos/dpf.png"/>
                </div>
                <div class="exhibitor-table-wrap">
                    <table class="exhibitor-details-table">
                        <tr>
                            <td class="name-cell" colspan="2">
                                <h2 class="exhibitor-name">3E DPF DOCTOR INDIA</h2>
                            </td>
                        </tr>
                        <tr class="hall-stall-row">
                            <td><span class="meta-label">Hall No :</span> 12</td>
                            <td><span class="meta-label">Stall No :</span> B8</td>
                        </tr>
                        <tr>
                            <td class="website-cell" colspan="2">
                                <a href="https://www.dpf-doctor.com" target="_blank">www.dpf-doctor.com</a>
                            </td>
                        </tr>
                    </table>
                </div>
            </article>

            <article class="exhibitor-card" id="exhibitor-1">
                <div class="exhibitor-logo-wrap">
                    <img class="exhibitor-logo" src="https://prawaas.com/storage/logos/tripeaz.png"/>
                </div>
                <div class="exhibitor-table-wrap">
                    <table class="exhibitor-details-table">
                        <tr>
                            <td class="name-cell" colspan="2">
                                <h2 class="exhibitor-name">TRIPEAZ</h2>
                            </td>
                        </tr>
                        <tr class="hall-stall-row">
                            <td><span class="meta-label">Hall No :</span> 12</td>
                            <td><span class="meta-label">Stall No :</span> B8</td>
                        </tr>
                        <tr>
                            <td class="website-cell" colspan="2">
                                <a href="https://tripeaztaxipartners.com/" target="_blank">tripeaztaxipartners.com</a>
                            </td>
                        </tr>
                    </table>
                </div>
            </article>
        </div>
    </body>
    </html>
    """
    records = extract_html_records(
        prawaas_fixture,
        "https://prawaas.com/prawaas-2026/public/exhibitor-list"
    )

    assert len(records) == 2

    # Verify 3E DPF DOCTOR INDIA
    assert records[0].company_name == "3E DPF DOCTOR INDIA"
    assert records[0].hall == "12"
    assert records[0].booth == "B8"
    assert records[0].website == "https://www.dpf-doctor.com"

    # Verify TRIPEAZ: Name must be TRIPEAZ (not website hostname tripeaztaxipartners.com)
    assert records[1].company_name == "TRIPEAZ"
    assert records[1].hall == "12"
    assert records[1].booth == "B8"
    assert records[1].website == "https://tripeaztaxipartners.com"


def test_ui_control_words_rejected_as_company_names():
    """Verify that UI controls such as 'Sort by', 'Filter by', 'Clear' are rejected as company names."""
    from app.extraction.html_records import _is_valid_company_name
    assert not _is_valid_company_name("Sort by")
    assert not _is_valid_company_name("Filter by")
    assert not _is_valid_company_name("Clear all")
    assert not _is_valid_company_name("Reset")
    assert not _is_valid_company_name("Show all")
    assert not _is_valid_company_name("Apply")
    assert not _is_valid_company_name("Categories")
    # Real company names remain valid
    assert _is_valid_company_name("Arvind Limited")
    assert _is_valid_company_name("Colorjet India Limited")


def test_spa_shell_detection_nextjs_app_router():
    """Verify that should_use_browser detects modern Next.js App Router SPA patterns."""
    from app.discovery.strategy import should_use_browser
    from app.models.site_profile import SiteProfile

    nextjs_profile = SiteProfile(
        url="https://exhibitor.example.com",
        is_html=True,
        initial_content="""
        <html>
            <head>
                <script src="/_next/static/chunks/main-app.js"></script>
            </head>
            <body>
                <script>(self.__next_f=self.__next_f||[]).push([0])</script>
            </body>
        </html>
        """,
    )
    assert should_use_browser(nextjs_profile) is True


def test_avatar_initials_avoided_and_combo_hall_booth_extracted():
    """Verify that avatar badges are bypassed for descriptive names and combined hall/booth patterns match."""
    html = """
    <div class="card-wrapper">
        <article class="exhibitor-card">
            <a href="/directory/123" class="avatar h-16 w-16">DO</a>
            <a href="/directory/123" class="company-title">Department of Handlooms & Textiles</a>
            <a href="mailto:info@handlooms.gov.in">info@handlooms.gov.in</a>
        </article>
        <div class="card-footer">
            <span>HALL 10 / 10-A3A</span>
        </div>
    </div>
    <div class="card-wrapper">
        <article class="exhibitor-card">
            <a href="/directory/124" class="avatar h-16 w-16">AL</a>
            <a href="/directory/124" class="company-title">Arvind Limited</a>
            <a href="mailto:contact@arvind.in">contact@arvind.in</a>
        </article>
        <div class="card-footer">
            <span>HALL 1GF / 1G-A4</span>
        </div>
    </div>
    """
    records = extract_html_records(html, "https://example.com/directory")
    assert len(records) == 2

    assert records[0].company_name == "Department of Handlooms & Textiles"
    assert records[0].hall == "10"
    assert records[0].booth == "10-A3A"
    assert records[0].email == "info@handlooms.gov.in"

    assert records[1].company_name == "Arvind Limited"
    assert records[1].hall == "1GF"
    assert records[1].booth == "1G-A4"
    assert records[1].email == "contact@arvind.in"


def test_browser_intercepted_api_extraction():
    """Verify that _execute_browser_strategy extracts records from high-scoring intercepted APIs."""
    from unittest.mock import MagicMock
    from app.agents.universal_scraper import _execute_browser_strategy
    from app.browser.models import BrowserRenderResult
    from app.models.scrape_result import ScrapeResult
    from app.models.scrape_budget import ScrapeBudget
    from app.models.site_profile import SiteProfile
    from datetime import datetime, timezone

    mock_client = MagicMock()
    mock_render = BrowserRenderResult(
        requested_url="https://example.com/directory",
        final_url="https://example.com/directory",
        status_code=200,
        rendered_html="<html><body><div id='app'></div></body></html>",
        intercepted_apis=[
            (
                "https://api.example.com/categories",
                {"data": [{"name": "1. Apparel"}, {"name": "2. Fabrics"}]}
            ),
            (
                "https://api.example.com/v1/buyer/exhibitors",
                {
                    "data": {
                        "totalPages": 1,
                        "items": [
                            {
                                "exhibitorName": "Arvind Limited",
                                "stallNumber": "1G-A4",
                                "hallNumber": "HALL 1GF",
                                "city": "Ahmedabad",
                                "email": "yatin.padia@arvind.in",
                                "website": "https://www.arvind.com"
                            },
                            {
                                "exhibitorName": "Auro Textiles",
                                "stallNumber": "2G-C1",
                                "hallNumber": "HALL 2GF",
                                "city": "Baddi",
                                "email": "ravi@vardhman.com",
                                "website": "https://vardhman.com"
                            }
                        ]
                    }
                }
            ),
        ]
    )
    mock_client.render.return_value = mock_render

    profile = SiteProfile(url="https://example.com/directory", is_html=True)
    res = ScrapeResult(source_url="https://example.com/directory", started_at=datetime.now(timezone.utc))
    budget = ScrapeBudget(max_pages=1, max_records=10)

    _execute_browser_strategy("https://example.com/directory", profile, res, budget, browser_client=mock_client)

    assert res.strategy == "BROWSER_API"
    assert len(res.records) == 2
    assert res.records[0].company_name == "Arvind Limited"
    assert res.records[0].booth == "1G-A4"
    assert res.records[0].hall == "HALL 1GF"
    assert res.records[0].email == "yatin.padia@arvind.in"
    assert res.records[1].company_name == "Auro Textiles"


def test_gated_landing_page_boilerplate_rejected():
    """Verify that marketing/gated landing pages with forms, stats, and widgets yield 0 records instead of garbage."""
    html = """
    <html>
    <body>
        <div class="elementor-widget-container">
            <h2>Download the IHE Directory</h2>
            <p>Enter your email to get the IHE Directory instantly.</p>
            <form>
                <label>Your Name</label><input type="text">
                <label>Email Address</label><input type="email">
                <button type="submit">Download</button>
            </form>
        </div>
        <div class="elementor-widget-container">
            <h2>Total Visitors</h2>
            <p>solidifying its standing as the preeminent expo</p>
        </div>
        <div class="elementor-widget-container">
            <h2>Stay Connected</h2>
            <a href="https://linkedin.com">Linkedin-in</a>
            <a href="/terms">Terms & Conditions</a>
        </div>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitor-directory/")
    assert len(records) == 0


def test_venue_descriptions_and_empty_table_not_extracted_as_companies():
    """Verify that event venue paragraphs and empty directory tables do not produce false-positive records."""
    html = """
    <html>
    <body>
        <div class="event-list" id="BindExhibitorsList">
            <table id="tblExlist" class="table"></table>
        </div>
        <div class="tg-eventspeaker">
            <div class="tg-contentbox">
                <div class="tg-eventhead">
                    <div class="tg-title">
                        <h2>PharmaTech Expo Chandigarh</h2>
                    </div>
                </div>
                <div class="tg-description">
                    <p>Parade Ground, Sector 17, Nr. ISBT, Chandigarh(UT), India</p>
                </div>
            </div>
        </div>
        <div class="tg-eventspeaker">
            <div class="tg-contentbox">
                <div class="tg-eventhead">
                    <div class="tg-title">
                        <h2>PharmaTech Expo Gandhinagar</h2>
                    </div>
                </div>
                <div class="tg-description">
                    <p>Helipad Exhibition Centre, Gandhinagar, Gujarat, India</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://pharmatechexpo.com/ExhibitorsList")
    assert len(records) == 0


def test_normalizer_maps_stall_name_and_segments():
    """Verify normalize_api_record maps stallName to booth and segments to category."""
    from app.extraction.normalizer import normalize_api_record
    raw = {
        "companyName": "3M India Limited",
        "stallName": "D3",
        "hallName": "1",
        "city": "Bengaluru",
        "country": "India",
        "segments": ["Pharma Machinery", "Packaging"],
    }
    record = normalize_api_record(raw, "https://api.example.com/exhibitors")
    assert record.company_name == "3M India Limited"
    assert record.booth == "D3"
    assert record.hall == "1"
    assert record.city == "Bengaluru"
    assert record.country == "India"
    assert record.category == ["Pharma Machinery", "Packaging"]


def test_generic_website_normalization_and_validation():
    """Verify generic website normalization handles all standard domain formats and rejects arbitrary text."""
    from app.extraction.normalizer import normalize_website, is_domain_or_url

    assert normalize_website("Www.Aarviengineering.in") == "https://www.aarviengineering.in"
    assert normalize_website("www.example.com") == "https://www.example.com"
    assert normalize_website("example.com") == "https://example.com"
    assert normalize_website("https://example.com/") == "https://example.com"
    assert normalize_website("http://example.com/") == "http://example.com"
    assert normalize_website("http://sub.example.co.uk/path/") == "http://sub.example.co.uk/path"

    # Reject non-domains / arbitrary text
    assert normalize_website("not-a-domain") is None
    assert normalize_website("Aarvi Engineering") is None
    assert normalize_website("Acme Pvt. Ltd.") is None
    assert normalize_website("manifest.json") is None

    # is_domain_or_url helper check
    assert is_domain_or_url("Www.Aarviengineering.in") is True
    assert is_domain_or_url("tripeaztaxipartners.com") is True
    assert is_domain_or_url("https://example.com") is True
    assert is_domain_or_url("Aarvi Engineering") is False
    assert is_domain_or_url("Acme Pvt. Ltd.") is False


def test_company_name_vs_website_domain_distinction():
    """Verify that domain text is never used as company name when heading or business name exists."""
    html = """
    <article class="exhibitor-card">
        <div class="card-content">
            <a href="https://tripeaztaxipartners.com" class="web-link">tripeaztaxipartners.com</a>
            <h3 class="name">TRIPEAZ</h3>
            <span>Stall: 10</span>
        </div>
    </article>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 1
    assert records[0].company_name == "TRIPEAZ"
    assert records[0].website == "https://tripeaztaxipartners.com"
    assert records[0].booth == "10"


def test_card_extraction_with_address_website_description_booth():
    """Verify that cards with address, booth details, text website, and description are fully structured."""
    html = """
    <div class="card exhibitor-detail">
        <h1>Aarvi Engineering</h1>
        <div class="info-grid">
            <div class="address-box">
                <h4>Address</h4>
                <p>Ground Floor - 52, Suprem Industrial Estate, Singarwa Road, Ahmedabad, Gujarat, India. Pincode - 382430</p>
            </div>
            <div class="booth-box">
                <h4>Booth Details</h4>
                <p>Hall: 1</p>
                <p>Stall: E50</p>
            </div>
            <div class="website-box">
                <h4>Website</h4>
                <p>Www.Aarviengineering.In</p>
            </div>
        </div>
        <div class="company-desc">
            <h4>Company Description</h4>
            <p>Aarvi Engineering is a trusted manufacturer and exporter of advanced bottle packaging machinery based in Ahmedabad.</p>
        </div>
    </div>
    <div class="card exhibitor-detail">
        <h1>Beta Technologies</h1>
        <div class="info-grid">
            <div class="address-box">
                <h4>Address</h4>
                <p>Plot 12, Industrial Area, Mumbai, Maharashtra, India</p>
            </div>
            <div class="booth-box">
                <h4>Booth Details</h4>
                <p>Hall: 2</p>
                <p>Stall: B20</p>
            </div>
            <div class="website-box">
                <h4>Website</h4>
                <p>https://www.betatech.com</p>
            </div>
        </div>
        <div class="company-desc">
            <h4>Company Description</h4>
            <p>Beta Technologies delivers automation and industrial sensor solutions.</p>
        </div>
    </div>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2

    aarvi = records[0]
    assert aarvi.company_name == "Aarvi Engineering"
    assert aarvi.website == "https://www.aarviengineering.in"
    assert aarvi.hall == "1"
    assert aarvi.booth == "E50"
    assert "Suprem Industrial Estate" in aarvi.address
    assert "trusted manufacturer" in aarvi.description

    beta = records[1]
    assert beta.company_name == "Beta Technologies"
    assert beta.website == "https://www.betatech.com"
    assert beta.hall == "2"
    assert beta.booth == "B20"
    assert "Industrial Area" in beta.address


def test_table_with_domain_column_distinguishes_name_and_website():
    """Verify table extraction correctly maps domain column to website and participant to company name."""
    html = """
    <table>
        <thead>
            <tr>
                <th>Domain Name</th>
                <th>Participant</th>
                <th>Hall No</th>
                <th>Stall No</th>
                <th>Address</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td>www.aarviengineering.in</td>
                <td>Aarvi Engineering</td>
                <td>1</td>
                <td>E50</td>
                <td>Ahmedabad, Gujarat</td>
            </tr>
            <tr>
                <td>tripeaztaxipartners.com</td>
                <td>TRIPEAZ</td>
                <td>4</td>
                <td>B8</td>
                <td>New Delhi, India</td>
            </tr>
        </tbody>
    </table>
    """
    records = extract_html_records(html, "https://example.com/directory")
    assert len(records) == 2
    assert records[0].company_name == "Aarvi Engineering"
    assert records[0].website == "https://www.aarviengineering.in"
    assert records[0].hall == "1"
    assert records[0].booth == "E50"
    assert records[0].address == "Ahmedabad, Gujarat"

    assert records[1].company_name == "TRIPEAZ"
    assert records[1].website == "https://tripeaztaxipartners.com"
    assert records[1].hall == "4"
    assert records[1].booth == "B8"


def test_duplicate_pagination_stops_immediately():
    """Verify scraper halts pagination loop immediately when a page returns identical records."""
    page_requests = {"count": 0}

    card_content = """
    <article class="exhibitor-card">
        <h2>Acme Robotics Pvt. Ltd.</h2>
        <span class="booth">Stall: A1</span>
    </article>
    <article class="exhibitor-card">
        <h2>Beta Technologies</h2>
        <span class="booth">Stall: A2</span>
    </article>
    """

    def mock_requests_get(url, *args, **kwargs):
        page_requests["count"] += 1
        # Server loops returning the exact same company on page 1, 2, 3...
        return type("Resp", (), {
            "ok": True,
            "status_code": 200,
            "text": card_content,
            "json": lambda self: {"items": [{"name": "Acme Robotics Pvt. Ltd."}, {"name": "Beta Technologies"}]},
        })()

    with patch("requests.get", mock_requests_get):
        with patch("app.agents.universal_scraper.discover_site") as mock_disc:
            p = SiteProfile(url="https://example.com/exhibitors", is_html=True)
            p.initial_content = f"{card_content}<a href='https://example.com/exhibitors?page=2'>Next</a>"
            p.has_repeated_elements = True
            p.has_pagination = True
            p.pagination_signals = [{"parameter": "page"}]
            mock_disc.return_value = p

            budget = ScrapeBudget(max_pages=50, max_requests=50)
            res = scrape("https://example.com/exhibitors", budget=budget)

            # Must stop on page 2 after discovering no new records, NOT run to 50!
            assert res.requests_made <= 2
            assert res.pages_scraped <= 2
            assert len(res.records) == 2
            assert res.duplicates_removed == 0


def test_quality_scoring_consistency():
    """Verify quality score is consistent across FULL, PARTIAL, and FAILED extractions."""
    # 1. FULL extraction
    full_records = [
        ExhibitorRecord(
            company_name="Acme Corp",
            website="https://acme.com",
            detail_url="https://example.com/ex/1",
            source_url="https://example.com",
        )
    ]
    q_full = evaluate_extraction_quality(
        records=full_records,
        pages_scraped=1,
        expected_pages=1,
    )
    assert q_full.quality_status == QualityStatus.COMPLETE
    assert q_full.quality_score >= 0.80

    # 2. PARTIAL extraction (only 1 page of 10 expected)
    q_partial = evaluate_extraction_quality(
        records=full_records,
        pages_scraped=1,
        expected_pages=10,
        budget_limited=True,
    )
    assert q_partial.quality_status == QualityStatus.PARTIAL
    # Incomplete directory coverage must be capped appropriately
    assert 0.50 <= q_partial.quality_score <= 0.75

    # 3. FAILED extraction (0 records)
    q_failed = evaluate_extraction_quality(
        records=[],
        scrape_error="HTTP 500 Server Error",
    )
    assert q_failed.quality_status == QualityStatus.FAILED
    assert q_failed.quality_score == 0.0


def test_offcanvas_navigation_and_share_buttons_ignored():
    """Verify that off-canvas navigation menus and share actions (WhatsApp, LINE, Xing) are ignored."""
    html = """
    <html>
    <body>
        <div class="o-off-canvas o-off-canvas--top">
            <ul class="o-off-canvas__list">
                <li class="o-off-canvas__item">
                    <a class="o-off-canvas__item-link" href="/en/planning-preparation.html">
                        <span class="o-off-canvas__item-title">Planning & Preparation</span>
                    </a>
                </li>
                <li class="o-off-canvas__item">
                    <a class="o-off-canvas__item-link" href="/en/themes.html">
                        <span class="o-off-canvas__item-title">Themes & Events</span>
                    </a>
                </li>
                <li class="o-off-canvas__item">
                    <span class="o-off-canvas__item-title">WhatsApp</span>
                </li>
                <li class="o-off-canvas__item">
                    <span class="o-off-canvas__item-title">Recommend page by email</span>
                </li>
                <li class="o-off-canvas__item">
                    <span class="o-off-canvas__item-title">Copy to clipboard</span>
                </li>
            </ul>
        </div>
        <div class="main-content">
            <div class="ex-exhibitor-search-result-item m-search-result-item">
                <h4><span>Alpha Fabrics Corp.</span></h4>
                <div class="ex-exhibitor-search-result-item__location">
                    <span class="location-text">12.1, F03</span>
                </div>
            </div>
            <div class="ex-exhibitor-search-result-item m-search-result-item">
                <h4><span>Beta Textil GmbH</span></h4>
                <div class="ex-exhibitor-search-result-item__location">
                    <span class="location-text">9.0, B20</span>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2
    names = [r.company_name for r in records]
    assert "Alpha Fabrics Corp." in names
    assert "Beta Textil GmbH" in names
    assert "Planning & Preparation" not in names
    assert "WhatsApp" not in names
    assert "Recommend page by email" not in names
    assert "Copy to clipboard" not in names

    assert records[0].hall == "12.1"
    assert records[0].booth == "F03"





