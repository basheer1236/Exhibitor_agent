import re
from typing import Optional
from urllib.parse import urlparse

from app.models.exhibitor import ExhibitorRecord
from app.extraction.normalizer import is_domain_or_url


EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
INDIAN_PHONE_PATTERN = re.compile(r"^[6-9]\d{9}$|^\d{10}$")


def is_valid_url_structure(url: Optional[str]) -> bool:
    """Validate that a URL has a valid HTTP/HTTPS scheme and netloc."""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def is_valid_website_structure(url: Optional[str]) -> bool:
    """Validate that a website URL has HTTP/HTTPS and a recognizable domain."""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            return False
        netloc = parsed.netloc
        return bool(netloc) and ("." in netloc or netloc.count(":") == 1)
    except Exception:
        return False


def is_valid_email_structure(email: Optional[str]) -> bool:
    """Validate basic email address structure."""
    if not email:
        return False
    return bool(EMAIL_PATTERN.match(email.strip()))


def is_valid_phone_structure(phone: Optional[str], country: Optional[str] = None) -> bool:
    """
    Validate phone number structure.
    If country is India or number matches Indian format, enforces 10 digits standard.
    For other countries, enforces standard international length (7 to 15 digits).
    """
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    is_india = (
        bool(country and country.strip().lower() == "india")
        or phone.startswith(("+91", "0091"))
        or (len(digits) == 12 and digits.startswith("91"))
        or (len(digits) == 11 and digits.startswith("0"))
        or (len(digits) == 10 and digits[0] in "6789")
    )
    if is_india:
        cand = digits
        if len(cand) == 12 and cand.startswith("91"):
            cand = cand[2:]
        elif len(cand) == 11 and cand.startswith("0"):
            cand = cand[1:]
        return len(cand) == 10 and bool(INDIAN_PHONE_PATTERN.match(cand))
    return 7 <= len(digits) <= 15


def validate_record(record: ExhibitorRecord) -> tuple[bool, list[str]]:
    """
    Perform structural validation on an extracted ExhibitorRecord.
    Returns (is_valid, issues).
    """
    issues = []

    # 1. Company name validation
    name = record.company_name.strip() if record.company_name else ""
    if not name:
        issues.append("Missing or empty company_name")
    elif len(name) < 2:
        issues.append(f"Company name too short: '{name}'")
    elif name.lower() in {"unknown", "n/a", "none", "null"}:
        issues.append(f"Generic/placeholder company name: '{name}'")
    elif is_domain_or_url(name):
        issues.append(f"Company name appears to be a domain/URL rather than a business name: '{name}'")

    # 2. Source URL validation
    if not record.source_url or not is_valid_url_structure(record.source_url):
        issues.append(f"Invalid or missing source_url: '{record.source_url}'")

    # 3. Detail URL validation (when present)
    if record.detail_url and not is_valid_url_structure(record.detail_url):
        issues.append(f"Invalid detail_url structure: '{record.detail_url}'")

    # 4. Website validation (when present)
    if record.website and not is_valid_website_structure(record.website):
        issues.append(f"Invalid website structure: '{record.website}'")

    # 5. Email validation (when present)
    if record.email and not is_valid_email_structure(record.email):
        issues.append(f"Invalid email structure: '{record.email}'")
    for em in record.emails:
        if not is_valid_email_structure(em):
            issues.append(f"Invalid email structure in emails: '{em}'")

    # 6. Phone validation (when present)
    if record.phone and not is_valid_phone_structure(record.phone, country=record.country):
        issues.append(f"Invalid phone structure (does not meet Indian 10-digit standard): '{record.phone}'")
    for ph in record.phones:
        if not is_valid_phone_structure(ph, country=record.country):
            issues.append(f"Invalid phone structure in phones: '{ph}'")

    return (len(issues) == 0, issues)
