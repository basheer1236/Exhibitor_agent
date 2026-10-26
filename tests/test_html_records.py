from app.extraction.html_records import extract_html_records


def test_extract_html_records():

    html = """
    <html>
        <body>
            <a href="/exhibitors/abc">
                ABC Technologies
            </a>

            <a href="/exhibitors/xyz">
                XYZ Solutions
            </a>

            <a href="/about">
                About Us
            </a>
        </body>
    </html>
    """

    records = extract_html_records(
        html,
        "https://example.com/exhibitors"
    )

    assert len(records) == 2

    assert records[0].company_name == (
        "ABC Technologies"
    )

    assert records[0].detail_url == (
        "https://example.com/exhibitors/abc"
    )

    assert records[1].company_name == (
        "XYZ Solutions"
    )


def test_cloudflare_email_protection_rejected():
    from app.extraction.html_records import _is_valid_company_name

    assert not _is_valid_company_name("[email protected]")
    assert not _is_valid_company_name("[email protected]")
    assert not _is_valid_company_name("email-protection")

    html = """
    <html>
        <body>
            <div class="contact">
                <a href="/cdn-cgi/l/email-protection#12345">[email protected]</a>
                <p>Phone: 9971365776</p>
            </div>
            <div class="main-boxs"><p>Acme Corp</p></div>
            <div class="main-boxs"><p>Beta Tech</p></div>
            <div class="main-boxs"><p>Gamma Energy</p></div>
        </body>
    </html>
    """
    records = extract_html_records(html, "https://example.com/exhibitors")
    names = [r.company_name for r in records]
    assert "[email protected]" not in names
    assert "Acme Corp" in names
    assert "Beta Tech" in names
    assert "Gamma Energy" in names


def test_bootstrap_row_offcanvas_not_treated_as_global_layout():
    from bs4 import BeautifulSoup
    from app.extraction.html_records import _is_inside_global_layout

    html = """
    <div class="row-offcanvas row-offcanvas-left" id="body_row_container_wrapper">
        <div class="main-boxs"><p>Valid Exhibitor</p></div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find("div", class_="main-boxs")
    assert not _is_inside_global_layout(box)



def test_filter_sidebar_excluded_and_wrapped_cards_extracted():
    """Verify sidebar filter tree items are rejected and grid cards wrapped in <a> are properly extracted."""
    html = """
    <html>
        <body>
            <div class="aside__Aside-sc-123">
                <div class="tree-filters__TreeWrapper-sc-456">
                    <div class="tree-filters__TreeItem-sc-789">Excipients</div>
                    <div class="tree-filters__TreeItem-sc-789">Natural Extracts</div>
                    <div class="tree-filters__TreeItem-sc-789">Generic APIs</div>
                    <div class="tree-filters__TreeItem-sc-789">Innovative APIs</div>
                </div>
            </div>
            <div class="results-container">
                <a href="/event/test-2026/exhibitor/RXhoaWJpdG9yXzE=">
                    <div class="style__UIBox-sc-1 grid__Wrapper-sc-2">
                        <span class="grid__Name-sc-3">AbbVie Contract Manufacturing</span>
                        <span class="grid__Type-sc-4">Featured Exhibitor</span>
                        <span class="grid__Booth-sc-5">10C17</span>
                    </div>
                </a>
                <a href="/event/test-2026/exhibitor/RXhoaWJpdG9yXzI=">
                    <div class="style__UIBox-sc-1 grid__Wrapper-sc-2">
                        <span class="grid__Name-sc-3">Ace Medical Co., Ltd.</span>
                        <span class="grid__Type-sc-4">Featured Exhibitor</span>
                        <span class="grid__Booth-sc-5">11F46</span>
                    </div>
                </a>
                <a href="/event/test-2026/exhibitor/RXhoaWJpdG9yXzM=">
                    <div class="style__UIBox-sc-1 grid__Wrapper-sc-2">
                        <span class="grid__Name-sc-3">Adare Pharma Solutions</span>
                        <span class="grid__Type-sc-4">Featured Exhibitor</span>
                        <span class="grid__Booth-sc-5">6F18</span>
                    </div>
                </a>
            </div>
        </body>
    </html>
    """
    records = extract_html_records(html, "https://visitor.example.com/event/test-2026/exhibitors")
    names = [r.company_name for r in records]
    assert "Excipients" not in names
    assert "Natural Extracts" not in names
    assert "AbbVie Contract Manufacturing" in names
    assert "Ace Medical Co., Ltd." in names
    assert "Adare Pharma Solutions" in names

    rec0 = next(r for r in records if r.company_name == "AbbVie Contract Manufacturing")
    assert rec0.booth == "10C17"
    assert "Featured Exhibitor" in rec0.category
    assert rec0.detail_url == "https://visitor.example.com/event/test-2026/exhibitor/RXhoaWJpdG9yXzE="