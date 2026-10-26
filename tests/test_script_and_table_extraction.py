import pytest
from bs4 import BeautifulSoup
from app.extraction.html_records import (
    _extract_from_scripts,
    _extract_from_tables,
    extract_html_records,
    _is_valid_company_name,
)
from app.discovery.site import discover_site
from app.discovery.strategy import should_use_browser
from app.models.site_profile import SiteProfile


def test_is_valid_company_name():
    assert _is_valid_company_name("ACE Designers Ltd") is True
    assert _is_valid_company_name("Bharat Fritz Werner") is True
    assert _is_valid_company_name("About MASSIA") is False
    assert _is_valid_company_name("About EXPO") is False
    assert _is_valid_company_name("Visitor Registration") is False
    assert _is_valid_company_name("Downloads") is False
    assert _is_valid_company_name("Quick Links") is False
    assert _is_valid_company_name("+91 9545500451") is False
    assert _is_valid_company_name("info@massia.org") is False
    assert _is_valid_company_name("example.com") is False
    assert _is_valid_company_name("https://example.com") is False
    assert _is_valid_company_name("") is False
    assert _is_valid_company_name("10") is False
    assert _is_valid_company_name("265") is False
    assert _is_valid_company_name("1") is False


def test_extract_from_scripts_various_formats():
    html = """
    <html>
    <body>
        <script>
        const exhibitors = [
            { name: "Alpha Tech Solutions", stalls: "A10, A11", hall: "Hall 1" },
            { company: "Beta Manufacturing", booth: "B20", website: "https://betamfg.com" },
            { stand: "C30", exhibitor: "Gamma Robotics" },
            { name: "+91 9876543210", stall: "D40" }, // Phone number - must be filtered
            { name: "About The Event", stall: "E50" } // Nav title - must be filtered
        ];
        </script>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    records = _extract_from_scripts(soup, "https://example.com/stalls")
    
    assert len(records) == 3
    names = [r.company_name for r in records]
    assert "Alpha Tech Solutions" in names
    assert "Beta Manufacturing" in names
    assert "Gamma Robotics" in names

    r_alpha = next(r for r in records if r.company_name == "Alpha Tech Solutions")
    assert r_alpha.booth == "A10, A11"
    assert r_alpha.hall == "Hall 1"

    r_beta = next(r for r in records if r.company_name == "Beta Manufacturing")
    assert r_beta.booth == "B20"
    assert r_beta.website == "https://betamfg.com"


def test_extract_from_tables():
    html = """
    <html>
    <body>
        <table id="exhibitor-table">
            <thead>
                <tr>
                    <th>Company Name</th>
                    <th>Stall No.</th>
                    <th>Hall</th>
                    <th>Website</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Apex Tooling Corp</td>
                    <td>S-101</td>
                    <td>2</td>
                    <td><a href="https://apextools.com">Website</a></td>
                </tr>
                <tr>
                    <td>Beacon Automation</td>
                    <td>S-102</td>
                    <td>2</td>
                    <td>https://beaconauto.com</td>
                </tr>
            </tbody>
        </table>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    records = _extract_from_tables(soup, "https://example.com/exhibitors")
    assert len(records) == 2
    assert records[0].company_name == "Apex Tooling Corp"
    assert records[0].booth == "S-101"
    assert records[0].hall == "2"
    assert records[0].website == "https://apextools.com"
    assert records[1].company_name == "Beacon Automation"
    assert records[1].booth == "S-102"
    assert records[1].website == "https://beaconauto.com"


def test_empty_table_with_script_dataset_fallback():
    """Verify that when a table is an empty shell in static HTML, script dataset is extracted."""
    html = """
    <html>
    <body>
        <div class="container">
            <nav>
                <a href="/about" class="link">About MASSIA</a>
                <a href="/register" class="link">Visitor Registration</a>
                <a href="/downloads" class="link">Downloads</a>
                <a href="/contact" class="link">+91 9545500451</a>
            </nav>
            <table id="table">
                <thead>
                    <tr><th>Company</th><th>Stall</th></tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        <script>
        const data = [
            { name: "ACE Designers Ltd", stalls: "A44, A45" },
            { name: "Aurangabad Foundry", stalls: "A176" }
        ];
        </script>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/stall.php")
    assert len(records) == 2
    assert records[0].company_name == "ACE Designers Ltd"
    assert records[0].booth == "A44, A45"
    assert records[1].company_name == "Aurangabad Foundry"
    assert records[1].booth == "A176"


def test_self_referential_link_not_detail_link(monkeypatch):
    """Verify that links pointing to the current page are not counted as detail links."""
    html = """
    <html>
    <head><title>Stalls</title></head>
    <body>
        <a href="https://example.com/stall.php">Stalls Directory</a>
        <a href="https://example.com/stall.php#top">Back to top</a>
    </body>
    </html>
    """
    from app.discovery import site
    from app.discovery.http import FetchResponse
    monkeypatch.setattr(
        site,
        "fetch_url",
        lambda url, **kwargs: FetchResponse(
            requested_url="https://example.com/stall.php",
            final_url="https://example.com/stall.php",
            status_code=200,
            content=html,
            content_type="text/html",
            headers={"content-type": "text/html"},
        ),
    )
    profile = discover_site("https://example.com/stall.php")
    assert profile.has_detail_links is False
    assert len(profile.detail_links) == 0


def test_empty_table_shell_triggers_browser_if_no_records():
    """Verify that an empty directory table shell triggers should_use_browser if 0 records extracted."""
    html = """
    <html>
    <body>
        <table id="table">
            <thead><tr><th>Exhibitor</th><th>Stall</th></tr></thead>
            <tbody></tbody>
        </table>
        <script src="/bundle.js"></script>
    </body>
    </html>
    """
    profile = SiteProfile(
        url="https://example.com/directory",
        is_html=True,
        initial_content=html,
    )
    # With 0 records in result, should escalate to browser
    assert should_use_browser(profile, None) is True


def test_table_with_serial_numbers_and_location():
    """Verify that tables with serial column '#' and 'Location' column are accurately parsed."""
    html = """
    <html>
    <body>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Company Name</th>
                    <th>Location</th>
                    <th>Stall No</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td class="serial">1</td>
                    <td class="company">4E JUNCTION</td>
                    <td class="loc">HANGAR B</td>
                    <td class="stall">411A</td>
                </tr>
                <tr>
                    <td class="serial">2</td>
                    <td class="company">ABHAY INDUSTRIES</td>
                    <td class="loc">HANGAR A</td>
                    <td class="stall">112</td>
                </tr>
            </tbody>
        </table>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2
    assert records[0].company_name == "4E JUNCTION"
    assert records[0].hall == "HANGAR B"
    assert records[0].booth == "411A"
    assert records[1].company_name == "ABHAY INDUSTRIES"
    assert records[1].hall == "HANGAR A"
    assert records[1].booth == "112"


def test_table_takes_precedence_over_nav_dropdowns():
    """Verify that structured directory table takes precedence over repeated nav dropdown items."""
    html = """
    <html>
    <body>
        <ul class="nav">
            <li class="dropdown"><a href="/1">About Us</a></li>
            <li class="dropdown"><a href="/2">Registration</a></li>
            <li class="dropdown"><a href="/3">Downloads</a></li>
            <li class="dropdown"><a href="/4">Contact Us</a></li>
        </ul>
        <table>
            <thead>
                <tr>
                    <th>Company</th>
                    <th>Booth</th>
                </tr>
            </thead>
            <tbody>
                <tr><td>Zeta Dynamics</td><td>Z-100</td></tr>
                <tr><td>Omega Power</td><td>O-200</td></tr>
            </tbody>
        </table>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/directory")
    assert len(records) == 2
    assert records[0].company_name == "Zeta Dynamics"
    assert records[0].booth == "Z-100"
    assert records[1].company_name == "Omega Power"
    assert records[1].booth == "O-200"


def test_script_json_dataset_with_hall_stall_and_unicode_escapes():
    """Verify that script JSON datasets with hall_number, stall_number, type, and unicode escapes are extracted."""
    html = r"""
    <html>
    <body>
        <table id="exhibitorTable"><thead><tr><th>Name</th></tr></thead><tbody></tbody></table>
        <script>
        const exhibitorsRaw = [
            {"name":"Gnani.ai","hall_number":"3","type":"Corporate (domestic)","stall_number":"3.2"},
            {"name":"World Food Programme India","hall_number":"5","type":"Country Representation","stall_number":"5F.15"},
            {"name":"AWS\/Amazon","hall_number":"4","type":"Corporate (domestic)","stall_number":"4.1"},
            {"name":"Punjab Information \u0026 Communication Tech Corp","hall_number":"5","type":"Government","stall_number":"5F.26"}
        ];
        </script>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/hall-wise-exhibitors")
    assert len(records) == 4

    rec_gnani = next(r for r in records if r.company_name == "Gnani.ai")
    assert rec_gnani.booth == "3.2"
    assert rec_gnani.hall == "3"
    assert rec_gnani.category == ["Corporate (domestic)"]

    rec_wfp = next(r for r in records if r.company_name == "World Food Programme India")
    assert rec_wfp.booth == "5F.15"
    assert rec_wfp.hall == "5"
    assert rec_wfp.category == ["Country Representation"]

    rec_aws = next(r for r in records if r.company_name == "AWS/Amazon")
    assert rec_aws.booth == "4.1"
    assert rec_aws.hall == "4"

    rec_punjab = next(r for r in records if "Punjab Information & Communication" in r.company_name)
    assert rec_punjab.booth == "5F.26"
    assert rec_punjab.hall == "5"


def test_ai_and_co_domain_company_names_valid():
    """Verify modern company names like Gnani.ai or Apolitical.co are valid, while urls are not."""
    assert _is_valid_company_name("Gnani.ai") is True
    assert _is_valid_company_name("Apolitical.co") is True
    assert _is_valid_company_name("AWS/Amazon") is True
    assert _is_valid_company_name("example.com") is False
    assert _is_valid_company_name("https://example.com") is False
    assert _is_valid_company_name("www.example.com") is False
    assert _is_valid_company_name("example.com/about") is False


def test_headerless_directory_table_extraction():
    """Verify that tables with no <th> headers but consistent valid company names are extracted."""
    html = """
    <html>
    <body>
        <table class="exhibitor-list-table">
            <tbody>
                <tr><td>ROOFSOL ENERGY PRIVATE LIMITED</td></tr>
                <tr><td>JURCHEN TECHNOLOGY INDIA PVT. LTD.</td></tr>
                <tr><td>GANESH GREEN BHARAT LTD.</td></tr>
                <tr><td>GRAPHICANA (3M INDIA LTD.)</td></tr>
                <tr><td>POWERONE MICROSYSTEM PVT. LTD.</td></tr>
                <tr><td>ABILITY AUTOMATION SOLUTIONS PVT. LTD.</td></tr>
            </tbody>
        </table>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 6
    names = [r.company_name for r in records]
    assert "ROOFSOL ENERGY PRIVATE LIMITED" in names
    assert "JURCHEN TECHNOLOGY INDIA PVT. LTD." in names
    assert "ABILITY AUTOMATION SOLUTIONS PVT. LTD." in names


def test_schema_org_breadcrumb_exclusion():
    """Verify that schema.org breadcrumbs and navigation items like ListItem 'Why Visit' are ignored."""
    html = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": "https://example.com/"
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": "Why Visit",
                    "item": "https://example.com/why-visit"
                }
            ]
        }
        </script>
    </head>
    <body>
        <table class="exhibitors">
            <thead>
                <tr><th>Company Name</th><th>Booth</th></tr>
            </thead>
            <tbody>
                <tr><td>Apex Solar Tech Pvt. Ltd.</td><td>A-101</td></tr>
                <tr><td>SunWave Renewables</td><td>B-202</td></tr>
            </tbody>
        </table>
    </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 2
    names = [r.company_name for r in records]
    assert "Apex Solar Tech Pvt. Ltd." in names
    assert "SunWave Renewables" in names
    assert "Why Visit" not in names
    assert "Home" not in names



