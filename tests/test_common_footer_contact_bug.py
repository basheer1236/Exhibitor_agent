import pytest
from app.models.exhibitor import ExhibitorRecord
from app.models.scrape_budget import ScrapeBudget
from app.extraction.detail_enricher import (
    extract_detail_fields,
    clean_global_organizer_contacts,
    enrich_records,
)
from app.extraction.html_records import extract_html_records


def test_1_exhibitor_local_contact_preserved_not_footer():
    """
    Test 1:
    HTML contains:
    Exhibitor A: email: a@example.com, phone: 1111111111
    Footer: email: organizer@example.com, phone: 9999999999
    Expected:
    Exhibitor A: email = a@example.com, phone = 1111111111
    NOT: organizer@example.com, 9999999999
    """
    html = """
    <html>
      <header>
        <div class="site-header">
          <a href="mailto:organizer@example.com">Contact Organizer</a>
          <a href="tel:9999999999">Call Organizer</a>
        </div>
      </header>
      <main>
        <div class="exhibitor-card">
          <h2>Exhibitor A</h2>
          <div class="contact-box">
            <a href="mailto:a@example.com">a@example.com</a>
            <a href="tel:1111111111">1111111111</a>
          </div>
        </div>
        <div class="exhibitor-card">
          <h2>Exhibitor B</h2>
          <div class="contact-box">
            <a href="mailto:b@example.com">b@example.com</a>
            <a href="tel:2222222222">2222222222</a>
          </div>
        </div>
      </main>
      <footer>
        <div class="site-footer">
          <a href="mailto:organizer@example.com">organizer@example.com</a>
          <a href="tel:9999999999">+91 9999999999</a>
        </div>
      </footer>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2
    rec = records[0]

    assert rec.company_name == "Exhibitor A"
    assert rec.email == "a@example.com"
    assert rec.phone == "1111111111"
    assert "organizer@example.com" not in rec.emails
    assert "9999999999" not in rec.phones

    rec_b = records[1]
    assert rec_b.company_name == "Exhibitor B"
    assert rec_b.email == "b@example.com"
    assert rec_b.phone == "2222222222"
    assert "organizer@example.com" not in rec_b.emails
    assert "9999999999" not in rec_b.phones


def test_2_exhibitor_no_contact_returns_null_not_footer():
    """
    Test 2:
    Exhibitor has no email/phone.
    Footer contains organizer email/phone.
    Expected:
    email = null, phone = null.
    Do NOT copy footer values.
    """
    html = """
    <html>
      <header>
        <div class="tg-header">
          <a href="mailto:expo@pharmatechnologyindex.com">expo@pharmatechnologyindex.com</a>
          <a href="tel:9377235673">+91 93772 35673</a>
        </div>
      </header>
      <section class="tg-sectionspace">
        <h2>3M India Limited</h2>
        <h5><b>Address</b></h5>
        <h6>5th Floor, Marksquare, Bengaluru</h6>
        <h5><b>Booth Details</b></h5>
        <h6>Hall:1 Stall:D3</h6>
      </section>
      <footer class="tg-footer">
        <div class="tg-footercolumn">
          <h3>Email</h3>
          <a href="mailto:expo@pharmatechnologyindex.com">expo@pharmatechnologyindex.com</a>
          <h3>Office Number</h3>
          <a href="tel:9377235673">+91-93-77235673</a>
        </div>
      </footer>
    </html>
    """
    detail = extract_detail_fields(html, "https://pharmatechexpo.com/ExhibitorCompany?Company=3M")
    assert detail["address"] is not None
    assert detail["booth"] == "D3"
    assert detail["hall"] == "1"
    assert detail["email"] is None
    assert detail["emails"] == []
    assert detail["phone"] is None
    assert detail["phones"] == []


def test_3_multiple_exhibitors_do_not_receive_organizer_footer():
    """
    Test 3:
    Multiple exhibitors on a page with organizer footer.
    Expected:
    Organizer contact information is not assigned to every exhibitor.
    """
    html = """
    <html>
      <header>
        <div class="top-nav">
          <a href="mailto:info@expo-organizer.org">info@expo-organizer.org</a>
          <a href="tel:8888888888">8888888888</a>
        </div>
      </header>
      <div class="container">
        <div class="exhibitor-card">
          <h3>Alpha Machinery</h3>
          <p>Booth A1</p>
        </div>
        <div class="exhibitor-card">
          <h3>Beta Automation</h3>
          <p>Booth B2</p>
        </div>
        <div class="exhibitor-card">
          <h3>Gamma Tools</h3>
          <p>Booth C3</p>
        </div>
      </div>
      <footer class="page-footer">
        <p>Contact Us: <a href="mailto:info@expo-organizer.org">info@expo-organizer.org</a></p>
        <p>Call: <a href="tel:8888888888">+91 8888888888</a></p>
      </footer>
    </html>
    """
    records = extract_html_records(html, "https://expo-organizer.org/directory")
    assert len(records) == 3

    # Run global organizer cleaner
    cleaned = clean_global_organizer_contacts(records, source_html=html, source_url="https://expo-organizer.org/directory")
    for r in cleaned:
        assert r.email is None
        assert r.emails == []
        assert r.phone is None
        assert r.phones == []


def test_4_legitimate_shared_exhibitor_contact_preserved():
    """
    Test 4:
    A legitimate exhibitor has the same phone/email as another exhibitor
    (e.g. sister companies sharing a corporate domain).
    Do not blindly remove it only because it repeats.
    """
    records = [
        ExhibitorRecord(
            company_name="Tata Steel Ltd",
            website="https://www.tatasteel.com",
            email="corporate@tata.com",
            emails=["corporate@tata.com"],
            phone="9820011111",
            phones=["9820011111"],
            source_url="https://example.com/exhibitors",
        ),
        ExhibitorRecord(
            company_name="Tata Motors Ltd",
            website="https://www.tatamotors.com",
            email="corporate@tata.com",
            emails=["corporate@tata.com"],
            phone="9820011111",
            phones=["9820011111"],
            source_url="https://example.com/exhibitors",
        ),
        ExhibitorRecord(
            company_name="Tata Power Ltd",
            website="https://www.tatapower.com",
            email="corporate@tata.com",
            emails=["corporate@tata.com"],
            phone="9820011111",
            phones=["9820011111"],
            source_url="https://example.com/exhibitors",
        ),
    ]

    source_html = "<html><body><footer><p>Organizer: expo@event.com</p></footer></body></html>"
    cleaned = clean_global_organizer_contacts(records, source_html=source_html, source_url="https://event.com/list")

    # Corporate shared contact must be preserved because it matches company domain and is not in footer
    assert len(cleaned) == 3
    for r in cleaned:
        assert r.email == "corporate@tata.com"
        assert r.phone == "9820011111"


def test_5_api_contact_priority_footer_does_not_overwrite():
    """
    Test 5:
    API contains exhibitor-specific email/phone.
    Expected:
    API values are used.
    Footer values must not overwrite them.
    """
    api_rec = ExhibitorRecord(
        company_name="Precision Instruments",
        email="contact@precision.com",
        emails=["contact@precision.com"],
        phone="9876543210",
        phones=["9876543210"],
        detail_url="https://expo.com/detail/1",
        source_url="https://api.expo.com/v1/exhibitors",
    )

    detail_html = """
    <html>
      <main>
        <h1>Precision Instruments</h1>
        <p>Address: 123 Industrial Area</p>
      </main>
      <footer>
        <a href="mailto:organizer@expo.com">organizer@expo.com</a>
        <a href="tel:919999999999">+91 9999999999</a>
      </footer>
    </html>
    """

    class DummyResp:
        content = detail_html
        final_url = "https://expo.com/detail/1"

    from unittest.mock import patch

    with patch("app.extraction.detail_enricher.fetch_url", return_value=DummyResp()):
        enriched, _ = enrich_records(
            [api_rec],
            budget=ScrapeBudget(max_requests=5),
            current_requests=0,
        )

    assert len(enriched) == 1
    r = enriched[0]
    # API values must remain intact!
    assert r.email == "contact@precision.com"
    assert r.emails == ["contact@precision.com"]
    assert r.phone == "9876543210"
    assert r.phones == ["9876543210"]
    assert "organizer@expo.com" not in r.emails
    assert "9999999999" not in r.phones


def test_regression_contact_cleanup_preserves_business_fields():
    """
    Regression Test 1:
    Proves that fixing global contact information does NOT remove other exhibitor fields.
    Input exhibitor has business fields (company_name, description, website, address, products)
    and an invalid global organizer email/phone.
    Expected:
    company_name != null
    description != null
    website != null
    address != null
    products != []
    email = null, phone = null.
    """
    record = ExhibitorRecord(
        company_name="Aarvi Engineering",
        description="Aarvi Engineering is a trusted manufacturer and exporter...",
        website="https://www.aarviengineering.in",
        address="Ground Floor - 52, Suprem Industrial Estate, Kathwada, Ahmedabad",
        products=["Automatic Sticker Labeling Machine", "Automatic servo Filling Machine"],
        email="expo@organizer-portal.com",
        emails=["expo@organizer-portal.com"],
        phone="9377235673",
        phones=["9377235673"],
        source_url="https://organizer-portal.com/exhibitors",
    )

    source_html = """
    <html>
      <footer>
        <a href="mailto:expo@organizer-portal.com">expo@organizer-portal.com</a>
        <a href="tel:9377235673">9377235673</a>
      </footer>
    </html>
    """

    cleaned = clean_global_organizer_contacts(
        [record],
        source_html=source_html,
        source_url="https://organizer-portal.com/exhibitors",
    )

    assert len(cleaned) == 1
    r = cleaned[0]
    # Business fields MUST NOT BE TOUCHED
    assert r.company_name == "Aarvi Engineering"
    assert r.description == "Aarvi Engineering is a trusted manufacturer and exporter..."
    assert r.website == "https://www.aarviengineering.in"
    assert r.address == "Ground Floor - 52, Suprem Industrial Estate, Kathwada, Ahmedabad"
    assert r.products == ["Automatic Sticker Labeling Machine", "Automatic servo Filling Machine"]
    # Only invalid footer contacts are cleared
    assert r.email is None
    assert r.emails == []
    assert r.phone is None
    assert r.phones == []


def test_regression_api_business_fields_survive_filtering():
    """
    Regression Test 2:
    API contains legitimate exhibitor-specific data:
    description, website, address, products.
    These must survive the contact filtering intact.
    """
    api_rec = ExhibitorRecord(
        company_name="A&D Instruments India Pvt. Ltd.",
        description="Direct subsidiary of A&D Company Limited Japan, manufacturing weighing products.",
        website="https://www.aanddindia.in",
        address="D - 48, Udyog Vihar, Phase V, Gurugram, Haryana",
        category=["Pharma Machinery", "Lab & Analytical Equipment"],
        products=["Tablet Metal Detector", "Checkweigher AD-4961 Series"],
        email="expo@organizer-fair.org",
        emails=["expo@organizer-fair.org"],
        phone="9988776655",
        phones=["9988776655"],
        source_url="https://organizer-fair.org/api/exhibitors",
    )

    source_html = """
    <html>
      <div class="site-footer">
        <a href="mailto:expo@organizer-fair.org">expo@organizer-fair.org</a>
        <a href="tel:9988776655">9988776655</a>
      </div>
    </html>
    """

    cleaned = clean_global_organizer_contacts(
        [api_rec],
        source_html=source_html,
        source_url="https://organizer-fair.org/api/exhibitors",
    )

    assert len(cleaned) == 1
    r = cleaned[0]
    assert r.company_name == "A&D Instruments India Pvt. Ltd."
    assert r.description == "Direct subsidiary of A&D Company Limited Japan, manufacturing weighing products."
    assert r.website == "https://www.aanddindia.in"
    assert r.address == "D - 48, Udyog Vihar, Phase V, Gurugram, Haryana"
    assert r.category == ["Pharma Machinery", "Lab & Analytical Equipment"]
    assert r.products == ["Tablet Metal Detector", "Checkweigher AD-4961 Series"]
    assert r.email is None
    assert r.phone is None


def test_regression_detail_page_website_preserved_when_footer_removed():
    """
    Regression Test 3:
    If a detail page contains:
    EXHIBITOR: Aarvi Engineering
    Website: https://www.aarviengineering.in
    Address: Kathwada, Ahmedabad
    Products: Automatic Sticker Labeling Machine
    Footer: expo@pharmatechnologyindex.com, 9377235673

    Expected:
    website: https://www.aarviengineering.in
    address: Kathwada, Ahmedabad
    products: ["Automatic Sticker Labeling Machine"]
    email: null
    phone: null
    """
    detail_html = """
    <html>
      <header class="site-header">
        <a href="mailto:expo@pharmatechnologyindex.com">Organizer Email</a>
        <a href="tel:9377235673">Organizer Phone</a>
      </header>
      <div class="profile">
        <h2>Aarvi Engineering</h2>
        <h5><b>Website</b></h5>
        <h6><a href="https://www.aarviengineering.in">www.aarviengineering.in</a></h6>
        <h5><b>Address</b></h5>
        <p>Ground Floor - 52, Suprem Industrial Estate, Kathwada, Ahmedabad</p>
        <h5><b>Company Description</b></h5>
        <p>Aarvi Engineering is a trusted manufacturer and exporter of advanced packaging machinery.</p>
        <h5><b>Company Products</b></h5>
        <div class="productCard">
          <h6><b>Name</b></h6>
          <p>Automatic Sticker Labeling Machine</p>
        </div>
      </div>
      <footer class="tg-footer">
        <a href="mailto:expo@pharmatechnologyindex.com">expo@pharmatechnologyindex.com</a>
        <a href="tel:9377235673">9377235673</a>
      </footer>
    </html>
    """

    data = extract_detail_fields(detail_html, "https://pharmatechexpo.com/ExhibitorCompany?Company=Aarvi")
    assert data["website"] == "https://www.aarviengineering.in"
    assert "Kathwada, Ahmedabad" in data["address"]
    assert "trusted manufacturer" in data["description"]
    assert "Automatic Sticker Labeling Machine" in data["products"]
    assert data["email"] is None
    assert data["emails"] == []
    assert data["phone"] is None
    assert data["phones"] == []


def test_regression_exhibitor_local_contact_in_detail_section_preserved():
    """
    Regression Test 4:
    If an exhibitor genuinely has:
    email: sales@aarviengineering.in
    phone: +91 9876543210
    inside the exhibitor-specific detail section, they MUST be preserved.
    """
    detail_html = """
    <html>
      <header class="tg-header">
        <a href="mailto:expo@pharmatechnologyindex.com">expo@pharmatechnologyindex.com</a>
        <a href="tel:9377235673">9377235673</a>
      </header>
      <div class="profile">
        <h2>Aarvi Engineering</h2>
        <h5><b>Website</b></h5>
        <h6><a href="https://www.aarviengineering.in">www.aarviengineering.in</a></h6>
        <h5><b>Email</b></h5>
        <p><a href="mailto:sales@aarviengineering.in">sales@aarviengineering.in</a></p>
        <h5><b>Phone</b></h5>
        <p><a href="tel:+919876543210">+91 9876543210</a></p>
      </div>
      <footer class="tg-footer">
        <a href="mailto:expo@pharmatechnologyindex.com">expo@pharmatechnologyindex.com</a>
        <a href="tel:9377235673">9377235673</a>
      </footer>
    </html>
    """

    data = extract_detail_fields(detail_html, "https://pharmatechexpo.com/ExhibitorCompany?Company=Aarvi")
    assert data["website"] == "https://www.aarviengineering.in"
    assert data["email"] == "sales@aarviengineering.in"
    assert "sales@aarviengineering.in" in data["emails"]
    assert data["phone"] == "+919876543210" or "9876543210" in data["phone"]
    assert "expo@pharmatechnologyindex.com" not in data["emails"]
    assert "9377235673" not in data["phones"]
