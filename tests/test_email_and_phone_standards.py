import pytest
from app.models.exhibitor import ExhibitorRecord
from app.extraction.normalizer import (
    extract_all_emails,
    normalize_email,
    extract_all_phones,
    normalize_phone,
    normalize_record,
)
from app.quality.validator import (
    validate_record,
    is_valid_email_structure,
    is_valid_phone_structure,
)


def test_multi_email_extraction():
    """Verify that every email in compound/comma-separated strings is cleanly extracted."""
    raw = "asinternational321@gmail.com,info@asinternational.co"
    emails = extract_all_emails(raw)
    assert emails == ["asinternational321@gmail.com", "info@asinternational.co"]

    # Conjunction 'and'
    raw2 = "manish@eastindiaco.org and logistics@eastindiaco.org"
    assert extract_all_emails(raw2) == ["manish@eastindiaco.org", "logistics@eastindiaco.org"]

    # Trailing commas and spaces
    raw3 = "rajesh.kumar@ashahandicrafts.com,david.tuscano@ashahandicrafts.com,"
    assert extract_all_emails(raw3) == ["rajesh.kumar@ashahandicrafts.com", "david.tuscano@ashahandicrafts.com"]

    # Truncated or invalid TLD dropped
    raw4 = "ahmarltf@gmail.com,accounts@eurocollection.in,shipping@eurocollecti"
    assert extract_all_emails(raw4) == ["ahmarltf@gmail.com", "accounts@eurocollection.in"]

    # Three emails
    raw5 = "bhawesh@anjaniexim.in,info@anjaniexim.in,anjaniexim.india@gmail.com"
    assert extract_all_emails(raw5) == ["bhawesh@anjaniexim.in", "info@anjaniexim.in", "anjaniexim.india@gmail.com"]


def test_primary_email_selection():
    """Verify primary email prioritizes business domain over generic providers."""
    raw = "asinternational321@gmail.com,info@asinternational.co"
    primary = normalize_email(raw)
    assert primary == "info@asinternational.co"

    # When all generic, takes first
    raw_generic = "user1@gmail.com, user2@yahoo.com"
    assert normalize_email(raw_generic) == "user1@gmail.com"


def test_indian_phone_10_digit_standards():
    """Verify Indian phone numbers are validated and extracted to exactly 10 digits."""
    # +91 prefix with hyphens
    assert extract_all_phones("+91-9876543210", country="India") == ["9876543210"]

    # +91 prefix with spaces
    assert extract_all_phones("+91 98765 43210") == ["9876543210"]

    # Leading zero (STD / trunk)
    assert extract_all_phones("09876543210") == ["9876543210"]

    # 91 prefix without plus (12 digits)
    assert extract_all_phones("919876543210") == ["9876543210"]

    # Plain 10 digits
    assert extract_all_phones("9876543210") == ["9876543210"]

    # Landline with STD code (e.g. Delhi 011 -> 10 digits)
    assert extract_all_phones("011-23456789", country="India") == ["1123456789"]

    # Multiple Indian phones in one field
    multi = "+91 98765 43210 / +91 91234 56789"
    assert extract_all_phones(multi, country="India") == ["9876543210", "9123456789"]

    # Invalid length (e.g. 5 digits) must be rejected under Indian standard
    assert extract_all_phones("12345", country="India") == []


def test_international_phone_preservation():
    """Verify non-Indian international phones are preserved and validated."""
    us_phone = "+1 (555) 0199"
    assert extract_all_phones(us_phone, country="USA") == ["+1 (555) 0199"]
    assert is_valid_phone_structure(us_phone, country="USA") is True

    # Indian phone validator enforces 10 digits
    assert is_valid_phone_structure("9876543210", country="India") is True
    assert is_valid_phone_structure("+91 9876543210", country="India") is True
    assert is_valid_phone_structure("12345", country="India") is False


def test_normalize_record_emails_and_phones():
    """Verify normalize_record populates both single and list fields."""
    rec = ExhibitorRecord(
        company_name="AS International",
        email="asinternational321@gmail.com,info@asinternational.co",
        phone="+91-9876543210 / +91-9123456789",
        country="India",
        source_url="https://example.com/exhibitors",
    )
    norm = normalize_record(rec)

    # All emails captured
    assert norm.emails == ["asinternational321@gmail.com", "info@asinternational.co"]
    # Single email is valid primary
    assert norm.email == "info@asinternational.co"

    # All 10-digit Indian phones captured
    assert norm.phones == ["9876543210", "9123456789"]
    assert norm.phone == "9876543210"

    # Structural validator passes with 0 issues!
    is_valid, issues = validate_record(norm)
    assert is_valid is True
    assert issues == []


def test_phone_extraction_from_sentence_text():
    """Verify phone extraction strips names, states, emails, and extracts pure 10-digit Indian phones."""
    s1 = "UTTAR PRADESH MR. SHARIQ AHMAD SIDDIQUI 2scollection@gmail.com +91-5912410109"
    assert extract_all_phones(s1) == ["5912410109"]

    s2 = "RAJASTHAN YASH JAIN 3brothersbackoffice@gmail.com +91-9950038888 Product Category: FURNITURE"
    assert extract_all_phones(s2) == ["9950038888"]


def test_card_extraction_mixed_info_row_text():
    """Verify html card extraction correctly extracts all fields without corrupting phones or booths."""
    from app.extraction.html_records import extract_html_records

    html = """
    <div class="ex-card">
      <div class="ex-card-body">
        <h4 class="ex-company-name">2 S COLLECTION</h4>
        <div class="ex-info">
          <div class="ex-info-row">
            <span class="ex-info-icon"><i class="fa-solid fa-location-dot"></i></span>
            <span class="ex-info-text">MORADABAD, UTTAR PRADESH</span>
          </div>
          <div class="ex-info-row">
            <span class="ex-info-icon"><i class="fa-solid fa-envelope"></i></span>
            <span class="ex-info-text"><a href="mailto:2scollection@gmail.com">2scollection@gmail.com</a></span>
          </div>
          <div class="ex-info-row">
            <span class="ex-info-icon"><i class="fa-solid fa-phone"></i></span>
            <span class="ex-info-text">+91-5912410109</span>
          </div>
          <div class="ex-info-row">
            <span class="ex-info-icon"><i class="fa-solid fa-globe"></i></span>
            <span class="ex-info-text"><a href="https://www.2scollection.com">www.2scollection.com</a></span>
          </div>
          <div class="ex-info-row">
            <span class="ex-info-icon"><i class="fa-solid fa-layer-group"></i></span>
            <span class="ex-info-text"><strong>Product Category:</strong> HOUSE WARES, TABLEWARE</span>
          </div>
        </div>
      </div>
      <div class="ex-card-footer">
        <div class="ex-footer-side left"><span class="footer-label">Hall 16</span></div>
        <div class="ex-footer-side right"><span class="footer-label">Stand J-01/07</span></div>
      </div>
    </div>
    """
    records = extract_html_records(html, "https://ihgfdelhifair.in/autumn/fairdirectory")
    assert len(records) == 1
    r = records[0]

    assert r.company_name == "2 S COLLECTION"
    assert r.website == "https://www.2scollection.com"
    assert r.email == "2scollection@gmail.com"
    assert r.emails == ["2scollection@gmail.com"]
    assert r.phone == "5912410109"
    assert r.phones == ["5912410109"]
    assert r.hall == "16"
    assert r.booth == "J-01/07"
    assert r.city == "MORADABAD"
    assert r.state == "UTTAR PRADESH"
    assert r.country == "India"
    assert "HOUSE WARES" in r.category

    is_valid, issues = validate_record(r)
    assert is_valid is True
    assert issues == []
