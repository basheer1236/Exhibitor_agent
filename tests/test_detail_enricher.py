import pytest
from app.models.exhibitor import ExhibitorRecord
from app.models.scrape_budget import ScrapeBudget
from app.extraction.detail_enricher import (
    discover_detail_urls_from_html,
    extract_detail_fields,
    associate_detail_urls,
    enrich_records,
    _is_external_company_website,
)


def test_is_external_company_website():
    # Valid external company website
    assert _is_external_company_website("https://www.acrifabgroup.com", "pharmatechexpo.com") is True
    assert _is_external_company_website("http://example.com/about", "directory.com") is True

    # Same domain as directory / organizer
    assert _is_external_company_website("https://pharmatechexpo.com/contact", "pharmatechexpo.com") is False
    assert _is_external_company_website("https://sub.pharmatechexpo.com", "pharmatechexpo.com") is False

    # Social media URLs
    assert _is_external_company_website("https://www.facebook.com/acrifab", "pharmatechexpo.com") is False
    assert _is_external_company_website("https://linkedin.com/company/acrifab", "pharmatechexpo.com") is False
    assert _is_external_company_website("https://twitter.com/acrifab", "pharmatechexpo.com") is False
    assert _is_external_company_website("https://instagram.com/acrifab", "pharmatechexpo.com") is False

    # Generic search/utility engines
    assert _is_external_company_website("https://maps.google.com/?q=acrifab", "pharmatechexpo.com") is False


def test_discover_detail_urls_from_js():
    html = """
    <script>
        var exhibitorDetailsBaseUrl = '/ExhibitorCompany';
        function OpenDetail(CompanyName) {
            window.open('/ExhibitorCompany?Company=' + encodeURIComponent(CompanyName));
        }
    </script>
    <div class="row">
        <h3><a href="javascript:void(0)" onclick="OpenDetail('Acrifab Group')">Acrifab Group</a></h3>
        <h3><a href="javascript:void(0)" onclick="OpenDetail('A&D Instruments')">A&D Instruments</a></h3>
    </div>
    """
    base_url = "https://pharmatechexpo.com/ExhibitorsList"
    mapping = discover_detail_urls_from_html(html, base_url)

    assert "acrifab group" in mapping
    assert mapping["acrifab group"] == "https://pharmatechexpo.com/ExhibitorCompany?Company=Acrifab%20Group"
    assert "a&d instruments" in mapping
    assert mapping["a&d instruments"] == "https://pharmatechexpo.com/ExhibitorCompany?Company=A%26D%20Instruments"


def test_discover_detail_urls_from_anchors():
    html = """
    <div>
        <a href="/exhibitors/detail/101">Alpha Systems</a>
        <a href="/company/beta-labs">Beta Labs Inc</a>
        <a href="/privacy-policy">Privacy Policy</a>
    </div>
    """
    base_url = "https://expo.org/directory"
    mapping = discover_detail_urls_from_html(html, base_url)

    assert "alpha systems" in mapping
    assert mapping["alpha systems"] == "https://expo.org/exhibitors/detail/101"
    assert "beta labs inc" in mapping
    assert mapping["beta labs inc"] == "https://expo.org/company/beta-labs"
    assert "privacy policy" not in mapping


def test_extract_detail_fields_labeled():
    html = """
    <html><body>
    <div class="profile">
        <h5 class="subtitle"><b>Address</b></h5>
        <h6 class="subtitle">123 Industrial Way, Phase 2, Silicon Valley, CA, USA</h6>

        <h5 class="subtitle"><b>Booth Details</b></h5>
        <h6 class="subtitle text-center">Hall:2 Stall:B45</h6>

        <h5 class="subtitle"><b>Website</b></h5>
        <h6 class="subtitle text-center"><a href="https://www.alphasystems.io">www.alphasystems.io</a></h6>

        <h5 class="subtitle"><b>Company Description</b></h5>
        <p class="m-t-30">Alpha Systems delivers cutting-edge automation and AI testing hardware for biotechnology laboratories worldwide.</p>

        <h5 class="subtitle"><b>Company Products</b></h5>
        <div class="col-md-12 productCard">
            <h6 class="title"><b>Name</b></h6>
            <p class="subtitle">Robotic Dispenser RX-100</p>
        </div>
        <div class="col-md-12 productCard">
            <h6 class="title"><b>Name</b></h6>
            <p class="subtitle">Liquid Handler Ultra</p>
        </div>

        <h5><b>Email</b></h5>
        <p><a href="mailto:info@alphasystems.io">info@alphasystems.io</a></p>

        <h5><b>Phone</b></h5>
        <p><a href="tel:+14085551234">+1 408 555 1234</a></p>
    </div>
    </body></html>
    """
    fields = extract_detail_fields(html, "https://expo.org/exhibitor/101")

    assert fields["website"] == "https://www.alphasystems.io"
    assert "123 Industrial Way" in fields["address"]
    assert fields["booth"] == "B45"
    assert fields["hall"] == "2"
    assert "Alpha Systems delivers cutting-edge automation" in fields["description"]
    assert fields["email"] == "info@alphasystems.io"
    assert fields["phone"] == "+1 408 555 1234"
    assert "Robotic Dispenser RX-100" in fields["products"]
    assert "Liquid Handler Ultra" in fields["products"]


def test_associate_detail_urls():
    html = """
    <script>var exhibitorDetailsBaseUrl = '/ExhibitorCompany';</script>
    <a href="javascript:void(0)" onclick="OpenDetail('Zeta Robotics')">Zeta Robotics</a>
    """
    base_url = "https://pharmatechexpo.com/ExhibitorsList"
    records = [
        ExhibitorRecord(company_name="Zeta Robotics", source_url=base_url),
        ExhibitorRecord(company_name="Gamma Pharma", source_url=base_url),
    ]

    associate_detail_urls(records, html, base_url)

    assert records[0].detail_url == "https://pharmatechexpo.com/ExhibitorCompany?Company=Zeta%20Robotics"
    assert records[1].detail_url == "https://pharmatechexpo.com/ExhibitorCompany?Company=Gamma%20Pharma"


def test_enrich_records_budget_bounded(monkeypatch):
    calls = []

    def mock_fetch(url, *args, **kwargs):
        calls.append(url)
        return type("Resp", (), {
            "status_code": 200,
            "content": """
            <h5>Website</h5><p><a href="https://example.com/company">company.com</a></p>
            <h5>Address</h5><p>456 Oak Street, City</p>
            """,
            "final_url": url,
        })()

    monkeypatch.setattr("app.extraction.detail_enricher.fetch_url", mock_fetch)

    records = [
        ExhibitorRecord(company_name="C1", detail_url="https://expo.org/c1", source_url="https://expo.org"),
        ExhibitorRecord(company_name="C2", detail_url="https://expo.org/c2", source_url="https://expo.org"),
        ExhibitorRecord(company_name="C3", detail_url="https://expo.org/c3", source_url="https://expo.org"),
    ]

    budget = ScrapeBudget(max_requests=2)
    enriched, reqs = enrich_records(records, budget=budget, current_requests=0)

    # Must only make 2 requests because budget.max_requests=2
    assert reqs == 2
    assert len(calls) == 2
    assert enriched[0].website == "https://example.com/company"
    assert enriched[0].address == "456 Oak Street, City"
    assert enriched[1].website == "https://example.com/company"
    # Third record was NOT fetched due to budget
    assert enriched[2].website is None


def test_extract_detail_fields_address_and_contacts():
    html = """
    <div class="row no-gutters mb-3">
        <div class="col-4"><div class="text-secondary">Website</div></div>
        <div class="col-8"><div class="profileResponse"><a href="https://www.3dgsinc.com">https://www.3dgsinc.com</a></div></div>
    </div>
    <div class="row no-gutters mb-3">
        <div class="col-4"><div class="text-secondary">Phone</div></div>
        <div class="col-8"><div class="profileResponse">150-591-6559-0</div></div>
    </div>
    <div class="row no-gutters mb-3">
        <div class="col-4"><div class="text-secondary">Address</div></div>
        <div class="col-8"><div class="profileResponse">5601<br />Balloon Fiesta Parkway NE, Suite B, 87113<br />Albuquerque, NM<br />United States</div></div>
    </div>
    """
    fields = extract_detail_fields(html, "https://portal2026.semiconindia.org/co/3d-glass-solutions")
    assert fields["website"] == "https://www.3dgsinc.com"
    assert fields["phone"] == "150-591-6559-0"
    assert "Albuquerque" in fields["address"]
    assert fields["city"] == "Albuquerque"
    assert fields["state"] == "NM"
    assert fields["country"] == "United States"


def test_enrich_records_with_city_state_country(monkeypatch):
    def mock_fetch(url, *args, **kwargs):
        return type("Resp", (), {
            "status_code": 200,
            "content": """
            <div class="col-4">Website</div><div class="col-8"><a href="https://acme.org">acme.org</a></div>
            <div class="col-4">Phone</div><div class="col-8">+1 555-123-4567</div>
            <div class="col-4">Address</div><div class="col-8">100 Tech Park<br />Austin, TX<br />United States</div>
            """,
            "final_url": url,
        })()

    monkeypatch.setattr("app.extraction.detail_enricher.fetch_url", mock_fetch)

    records = [
        ExhibitorRecord(
            company_name="Acme Corp",
            detail_url="https://portal.org/co/acme",
            source_url="https://portal.org/exhibitors"
        )
    ]
    budget = ScrapeBudget(enrich_details=True, max_requests=10)
    enriched, count = enrich_records(records, budget=budget, current_requests=0)

    assert count == 1
    assert enriched[0].website == "https://acme.org"
    assert enriched[0].phone == "+1 555-123-4567"
    assert enriched[0].city == "Austin"
    assert enriched[0].state == "TX"
    assert enriched[0].country == "United States"


def test_extract_detail_fields_card_column_layout():
    """
    Test extraction of exhibitor detail pages where labels and values are organized
    into column cards (e.g. PharmaTech Expo), with plain text websites inside heading/subtitle elements.
    """
    html = """
    <div class="row">
        <div class="col-md-4" style="margin-top:15px;display: inline-grid;">
            <h5 class="title"><b>Address</b></h5>
            <h6 class="subtitle">Dhanraj Mahal, A-Wing, 2nd Floor, Mumbai, Maharashtra, India. Pincode - 400001</h6>
        </div>
        <div class="col-md-4" style="margin-top:15px;display: inline-grid;">
            <h5 class="title text-center">Booth Details</h5>
            <h6 class="subtitle text-center">
                <b>Hall:</b> 2
                <br/>
                <b>Stall:</b> A122
            </h6>
        </div>
        <div class="col-md-4" style="margin-top:15px;display: inline-grid;">
            <h5 class="title text-center"><b>Website</b></h5>
            <h6 class="subtitle text-center">www.tsaprocessequipments.com</h6>
        </div>
        <div class="col-md-12" style="margin-top:10px;">
            <h5 class="subtitle"><b>Company Description</b></h5>
            <p class="m-t-30 text-justify">
                TSA Process Equipments Pvt. Ltd. provides total water solutions for potable, process water and USP grade water.
            </p>
        </div>
    </div>
    """
    fields = extract_detail_fields(html, "https://pharmatechexpo.com/ExhibitorCompany?Company=TSA%20Process%20Equipments%20Pvt%20Ltd")
    assert fields["website"] == "https://www.tsaprocessequipments.com"
    assert "Dhanraj Mahal" in fields["address"]
    assert fields["hall"] == "2"
    assert fields["booth"] == "A122"
    assert "total water solutions" in fields["description"]

