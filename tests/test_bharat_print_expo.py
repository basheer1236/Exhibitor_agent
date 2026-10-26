import pytest
from app.discovery.api import looks_like_api, find_api_candidates, evaluate_api_payload
from app.extraction.api_records import extract_api_records, find_record_collection
from app.extraction.detail_enricher import associate_detail_urls, extract_detail_fields
from app.models.exhibitor import ExhibitorRecord


def test_reject_wordpress_core_endpoints_and_payloads():
    # WordPress core metadata endpoints must be rejected by looks_like_api
    wp_page_endpoint = "https://www.bharatprintexpo.com/wp-json/wp/v2/pages/7450"
    wp_post_endpoint = "https://example.com/wp-json/wp/v2/posts/123"
    assert looks_like_api(wp_page_endpoint) is False
    assert looks_like_api(wp_post_endpoint) is False

    # WordPress custom endpoints must be accepted
    custom_endpoint = "https://www.bharatprintexpo.com/wp-json/customapi/exibitor_list?event=19"
    assert looks_like_api(custom_endpoint) is True

    # WordPress core page object payload must be rejected by evaluate_api_payload
    wp_page_payload = {
        "id": 7450,
        "type": "page",
        "title": {"rendered": "Bharat Print Expo 2026 - Exhibitor List"},
        "content": {"rendered": "<div>...</div>"},
        "yoast_head_json": {
            "schema": {
                "@graph": [
                    {"@type": "WebPage", "name": "Bharat Print Expo"},
                    {"@type": "WebSite", "name": "Bharat Print Expo"},
                ]
            }
        }
    }
    res = evaluate_api_payload(wp_page_payload, wp_page_endpoint)
    assert res["valid_exhibitor_api"] is False


def test_customapi_payload_extraction_and_contact_fallbacks():
    sample_payload = [
        [
            {
                "id": "3256",
                "company_name": "AB-Tech Engineering & Co",
                "address": "119, Kannimar Nagar, Coimbatore-641006, Tamil Nadu, India",
                "room_no": "D90",
                "state": "33",
                "overseas_profile": "Post press Machineries manufacturer",
                "status": "Confirmed",
            },
            {
                "id": "3187",
                "company_name": "Konica Minolta",
                "address": "Gurgaon, India",
                "room_no": "D96",
                "overseas_profile": "Trusted leader. Customer care 18002662525. To know more, visit at bt.konicaminolta.in",
                "status": "Confirmed",
            }
        ],
        []
    ]
    endpoint = "https://www.bharatprintexpo.com/wp-json/customapi/exibitor_list?event=19"
    records = extract_api_records(sample_payload, endpoint)
    assert len(records) == 2

    # First record
    assert records[0].company_name == "AB-Tech Engineering & Co"
    assert records[0].booth == "D90"
    assert records[0].exhibitor_id == "3256"
    assert records[0].description == "Post press Machineries manufacturer"

    # Second record with contact fallbacks from profile
    assert records[1].company_name == "Konica Minolta"
    assert records[1].booth == "D96"
    assert records[1].phone == "18002662525"
    assert "konicaminolta.in" in (records[1].website or "")


def test_associate_detail_urls_from_js_company_id():
    html_content = """
    <script>
    if (company.overseas_profile && company.overseas_profile.trim() !== '') {
        linkStart = "<a href='/showcompanydetails.php?id=" + company.id + "' target='_blank'>";
        linkEnd = "</a>";
    }
    </script>
    """
    records = [
        ExhibitorRecord(company_name="AB-Tech", exhibitor_id="3256", source_url="https://www.bharatprintexpo.com"),
    ]
    associate_detail_urls(records, html_content, "https://www.bharatprintexpo.com/bpe-2026-exhibitors/")

    assert records[0].detail_url == "https://www.bharatprintexpo.com/showcompanydetails.php?id=3256"


def test_extract_detail_fields_bharat_print_expo():
    detail_html = """
    <html><body>
    <h2>AB-Tech Engineering & Co</h2>
    <div class="stall-badge">Stall No: D90</div>
    <h5>Company Overview</h5>
    <p>Post press Machineries manufacturer</p>
    <h5>Products on Display</h5>
    <div>
        1. Numbering machine
        2. Creasing machine
        3. Lamination machine
    </div>
    <h5>Office Location</h5>
    <p>119, Kannimar Nagar, Sathy Main Road, Ganapathy, Coimbatore-641006, Tamil Nadu, India</p>
    <a class="btn-visit" href="https://www.abtechengg.com" target="_blank">
        Visit Official Website
    </a>
    </body></html>
    """
    fields = extract_detail_fields(detail_html, "https://www.bharatprintexpo.com/showcompanydetails.php?id=3256")
    assert fields["website"] == "https://www.abtechengg.com"
    assert "Post press Machineries manufacturer" in fields["description"]
    assert "Coimbatore-641006" in fields["address"]
    assert len(fields["products"]) >= 3


def test_multi_urls_and_email_website():
    from app.extraction.normalizer import normalize_website

    # Comma-separated URLs with whitespace
    assert normalize_website("https://www.redingtongroup.com , www.hp.com") == "https://www.redingtongroup.com"
    assert normalize_website("https://www.usedoffsetprintingmachine.com, www.tissuenapkinmachines.com") == "https://www.usedoffsetprintingmachine.com"
    
    # Scheme-prefixed email URL
    assert normalize_website("https://info@twin-ringwire.com") == "https://twin-ringwire.com"
    
    # Plain email should still NOT be treated as a website
    assert normalize_website("info@twin-ringwire.com") is None
