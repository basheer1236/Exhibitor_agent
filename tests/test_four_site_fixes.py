import pytest
from bs4 import BeautifulSoup
from app.extraction.html_records import (
    extract_html_records,
    _is_valid_company_name,
    _extract_from_tables,
    _extract_from_lists,
)


def test_tailwind_classes_dont_crash_soup_select():
    """
    Test Issue 4 (mmiconnect / IFAT 2026):
    Tailwind utility classes like 'hover:bg-hover' must not crash with SelectorSyntaxError.
    """
    html = """
    <html><body>
        <div class="hover:bg-hover layout-image overflow-hidden relative rounded shadow-8">
            <h3>Alpha Technologies Inc.</h3>
            <a href="/exhibitor/alpha">Profile</a>
        </div>
        <div class="hover:bg-hover layout-image overflow-hidden relative rounded shadow-8">
            <h3>Beta Solutions Ltd.</h3>
            <a href="/exhibitor/beta">Profile</a>
        </div>
        <div class="hover:bg-hover layout-image overflow-hidden relative rounded shadow-8">
            <h3>Gamma Industries GmbH</h3>
            <a href="/exhibitor/gamma">Profile</a>
        </div>
    </body></html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    assert len(records) == 3
    names = [r.company_name for r in records]
    assert "Alpha Technologies Inc." in names
    assert "Beta Solutions Ltd." in names
    assert "Gamma Industries GmbH." in names or "Gamma Industries GmbH" in names


def test_img_alt_logo_grid_extraction():
    """
    Test Issue 1 (Windergy India):
    Exhibitors shown as logo cards with <img alt="Company Name"> and external website links.
    """
    html = """
    <html><body>
        <div class="container">
            <div class="col-6 col-lg-2">
                <a href="https://www.adaniwind.com/" target="_blank">
                    <picture>
                        <img alt="adani wind" class="lazyload" src="/logo1.jpg" />
                    </picture>
                </a>
            </div>
            <div class="col-6 col-lg-2">
                <a href="https://absgroup.in/" target="_blank">
                    <picture>
                        <img alt="Advanced Bolting Solutions Pvt.Ltd." class="lazyload" src="/logo2.jpg" />
                    </picture>
                </a>
            </div>
            <div class="col-6 col-lg-2">
                <a href="https://9torc.com/" target="_blank">
                    <picture>
                        <img alt="9 TORC" class="lazyload" src="/logo3.jpg" />
                    </picture>
                </a>
            </div>
        </div>
    </body></html>
    """
    records = extract_html_records(html, "https://www.windergy.in/exhibitor-list")
    assert len(records) == 3
    names = {r.company_name for r in records}
    assert "adani wind" in names
    assert "Advanced Bolting Solutions Pvt.Ltd." in names
    assert "9 TORC" in names

    # Check websites are properly captured
    rec_adani = next(r for r in records if r.company_name == "adani wind")
    assert rec_adani.website in ("https://www.adaniwind.com", "https://www.adaniwind.com/")


def test_directory_list_extraction_ul_li():
    """
    Test Issue 2 (IIT Expo):
    Exhibitors listed inside <ul><li> lists.
    """
    html = """
    <html><body>
        <div class="wpb_wrapper">
            <ul>
                <li>Anesta Iwata Motherson Ltd.</li>
                <li>Atlas Copco Compressor Sales</li>
                <li>Bosch Power Tools Ltd.</li>
                <li>Caterpillar Electric Pvt. Ltd.</li>
                <li>Grindwell Norton Ltd.</li>
                <li>Haas Technical Centre Pvt. Ltd.</li>
                <li>Habonim Vaas Automation Pvt. Ltd.</li>
            </ul>
        </div>
    </body></html>
    """
    records = extract_html_records(html, "https://iitexpo.com/exhibitor-list/")
    assert len(records) >= 6
    names = [r.company_name for r in records]
    assert "Anesta Iwata Motherson Ltd." in names
    assert "Bosch Power Tools Ltd." in names
    assert "Grindwell Norton Ltd." in names


def test_form_tables_and_prompts_are_rejected():
    """
    Test Issue 3 (CIOE China):
    Inquiry / appointment form tables with input fields and prompt labels ending with colons
    must be rejected and NOT extracted as exhibitor records.
    """
    html = """
    <html><body>
        <form id="negotiation-form">
            <table class="zs_type">
                <tr><th> company：</th><td><input type="text" name="company" /></td></tr>
                <tr><th> Phone Number：</th><td><input type="text" name="phone" /></td></tr>
                <tr><th> Email：</th><td><input type="text" name="email" /></td></tr>
                <tr><th> Negotiation Date：</th><td><input type="text" name="date" /></td></tr>
                <tr><th> Remarks：</th><td><textarea name="remarks"></textarea></td></tr>
                <tr><th> Verification Code：</th><td><input type="text" name="code" /></td></tr>
            </table>
        </form>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    table_records = _extract_from_tables(soup, "https://exhibitors.cioe.cn/gwen/index.html")
    assert len(table_records) == 0

    # Ensure validation function rejects prompt labels
    assert not _is_valid_company_name(" company：")
    assert not _is_valid_company_name("Phone Number：")
    assert not _is_valid_company_name("Phone Number")
    assert not _is_valid_company_name("Verification Code")
    assert not _is_valid_company_name("Negotiation Date")
    assert not _is_valid_company_name("Remarks")


def test_table_with_title_banner_header():
    """
    Test Issue (Papexpo India):
    Tables with a single-cell title banner on row 0 (e.g. <td colspan="3">LIST OF EXHIBITORS</td>)
    followed by the column headers on row 1 (e.g. S.NO, COMPANY NAME, Stall No.).
    """
    html = """
    <html><body>
        <table>
            <tr><td colspan="3">LIST OF EXHIBITORS</td></tr>
            <tr><td>S.NO</td><td>COMPANY NAME</td><td>Stall No.</td></tr>
            <tr><td>1</td><td>KLM PACKAGING INDUSTRIES</td><td>A1</td></tr>
            <tr><td>2</td><td>STICK MAIL PRODUCTS PRIVATE LIMITED</td><td>A2</td></tr>
            <tr><td>3</td><td>S V S TECHNOLOGIES</td><td>A3</td></tr>
        </table>
    </body></html>
    """
    records = extract_html_records(html, "https://papexpo.in/exhibiting-exhibitors/")
    assert len(records) == 3
    names = {r.company_name for r in records}
    assert "KLM PACKAGING INDUSTRIES" in names
    assert "STICK MAIL PRODUCTS PRIVATE LIMITED" in names
    assert "S V S TECHNOLOGIES" in names

    rec_klm = next(r for r in records if r.company_name == "KLM PACKAGING INDUSTRIES")
    assert rec_klm.booth == "A1"

