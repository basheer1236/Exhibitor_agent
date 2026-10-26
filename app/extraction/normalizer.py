import html
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse, urljoin

from app.models.exhibitor import ExhibitorRecord


UNICODE_SPACES_PATTERN = re.compile(
    r"[\s\u00a0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u3000\ufeff]+"
)
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")


def normalize_string(val: Optional[str]) -> Optional[str]:
    """Clean HTML entities, decode JS unicode escapes/slashes, and collapse whitespace."""
    if val is None:
        return None
    val_str = str(val)
    unescaped = html.unescape(val_str)
    if r"\u" in unescaped:
        try:
            unescaped = re.sub(
                r"\\u([0-9a-fA-F]{4})",
                lambda m: chr(int(m.group(1), 16)),
                unescaped,
            )
        except Exception:
            pass
    if r"\/" in unescaped:
        unescaped = unescaped.replace(r"\/", "/")
    no_zero_width = ZERO_WIDTH_PATTERN.sub("", unescaped)
    cleaned = UNICODE_SPACES_PATTERN.sub(" ", no_zero_width).strip()
    return cleaned if cleaned else None


def normalize_company_name(name: Optional[str]) -> str:
    """
    Normalize company name while preserving meaningful punctuation
    such as '.', ',', '&', '-', etc. (e.g. 'ABC Pvt. Ltd.').
    """
    cleaned = normalize_string(name)
    if not cleaned:
        return ""
    # Strip accidental outer quotes
    cleaned = cleaned.strip("\"'`*")
    return cleaned


def normalize_url(url: Optional[str], base_url: Optional[str] = None) -> Optional[str]:
    """
    Safely normalize URL:
    - Resolves relative URLs using base_url
    - Strips whitespace
    - Strips fragments (#...)
    - Lowercases scheme and netloc
    - Strips non-root trailing slash from path
    - Preserves meaningful query parameters
    """
    cleaned = normalize_string(url)
    if not cleaned:
        return None

    if base_url:
        cleaned = urljoin(base_url, cleaned)
    elif not cleaned.startswith(("http://", "https://")) and "." in cleaned:
        cleaned = "https://" + cleaned

    try:
        parsed = urlparse(cleaned)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        # Only process http/https schemes
        if scheme not in {"http", "https"}:
            return cleaned

        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        # Preserve query string exactly, omit fragment
        normalized = urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            ""  # strip fragment
        ))
        return normalized
    except Exception:
        return cleaned


GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "rediffmail.com", "icloud.com", "mail.com", "aol.com", "yandex.com",
}

EMAIL_EXTRACTION_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def extract_all_emails(val: Optional[object]) -> list[str]:
    """
    Deterministically extract all distinct, structurally valid email addresses
    from a string (possibly containing multiple comma-, whitespace-, or 'and'-separated emails)
    or list of strings.
    """
    if not val:
        return []
    items = val if isinstance(val, (list, tuple, set)) else [val]
    found = []
    for item in items:
        if not item:
            continue
        item_str = str(item)
        if item_str.lower().startswith("mailto:"):
            item_str = item_str[7:].strip()
        matches = EMAIL_EXTRACTION_REGEX.findall(item_str)
        for m in matches:
            cleaned = m.rstrip(".,;:*\"'")
            parts = cleaned.split(".")
            # Ensure valid TLD (at least 2 letters, e.g. .com, .in, .co)
            if len(parts) >= 2 and len(parts[-1]) >= 2 and parts[-1].isalpha():
                cleaned_lower = cleaned.lower()
                if cleaned_lower not in found:
                    found.append(cleaned_lower)
    return found


def normalize_email(email: Optional[str]) -> Optional[str]:
    """
    Extract the primary valid email from an email string (even if compound/multi-email).
    Prioritizes corporate/domain email addresses over generic providers if multiple exist.
    """
    emails = extract_all_emails(email)
    if not emails:
        return None
    # Prioritize corporate email
    for em in emails:
        dom = em.split("@")[-1]
        if dom not in GENERIC_EMAIL_DOMAINS:
            return em
    return emails[0]


def normalize_emails(val: Optional[object]) -> list[str]:
    """Return all distinct normalized valid emails."""
    return extract_all_emails(val)


PHONE_TOKEN_REGEX = re.compile(
    r"(?:(?:\+|00)91[\-\s]?)?[0]?[6-9]\d{9}\b|"
    r"(?:(?:\+|00)91[\-\s]?)?[0]?[1-9]\d{1,4}[\-\s]?\d{5,8}\b|"
    r"\+[1-9]\d{0,3}[\s\-]?(?:\(\d{1,4}\)[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}\b"
)

INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "delhi", "new delhi", "chandigarh", "jammu and kashmir", "ladakh", "puducherry",
}


def extract_all_phones(val: Optional[object], country: Optional[str] = None) -> list[str]:
    """
    Extract all distinct valid phone numbers from a string (which may contain multiple numbers
    separated by commas, slashes, 'and', or embedded within mixed text) or list of strings.
    If country is India (or the phone matches Indian format +91 / 91 / 0 / 10 digits starting with 1-9),
    validates and normalizes to exactly 10 digits according to Indian standards.
    """
    if not val:
        return []
    items = val if isinstance(val, (list, tuple, set)) else [val]
    is_india = bool(country and country.strip().lower() == "india")
    results = []

    for item in items:
        if not item:
            continue
        item_str = str(item).strip()
        if item_str.lower().startswith("tel:"):
            item_str = item_str[4:].strip()

        # If item_str contains words, emails, or mixed text, extract matching phone tokens via regex
        has_alpha_or_email = bool(re.search(r"[a-zA-Z@]", item_str))
        if has_alpha_or_email:
            candidates = PHONE_TOKEN_REGEX.findall(item_str)
        else:
            candidates = re.split(r"[,;/|\n]+|\band\b", item_str, flags=re.I)

        for cand in candidates:
            cand = cand.strip()
            if not cand:
                continue
            digits = re.sub(r"\D", "", cand)
            if not digits:
                continue

            # Detect if this candidate phone is Indian
            is_cand_indian = (
                is_india
                or cand.startswith("+91")
                or cand.startswith("0091")
                or (len(digits) == 12 and digits.startswith("91"))
                or (len(digits) == 11 and digits.startswith("0") and digits[1] in "123456789")
                or (len(digits) == 10 and digits[0] in "123456789")
            )

            if is_cand_indian:
                cand_digits = digits
                if len(cand_digits) == 12 and cand_digits.startswith("91"):
                    cand_digits = cand_digits[2:]
                elif len(cand_digits) == 11 and cand_digits.startswith("0"):
                    cand_digits = cand_digits[1:]

                # 10 digits Indian standard or 11 digits Indian toll-free (1800 / 1860)
                if len(cand_digits) == 10 or (len(cand_digits) == 11 and cand_digits.startswith(("1800", "1860"))):
                    if cand_digits not in results:
                        results.append(cand_digits)
            else:
                # International number (must not contain letters or @)
                if 7 <= len(digits) <= 15 and not re.search(r"[a-zA-Z@]", cand):
                    clean_int = re.sub(r"\s+", " ", cand).strip()
                    if clean_int not in results:
                        results.append(clean_int)

    return results


def normalize_phone(phone: Optional[str], country: Optional[str] = None) -> Optional[str]:
    """
    Normalize phone number, checking 10 digits for Indian standard when applicable.
    Returns primary phone string.
    """
    phones = extract_all_phones(phone, country=country)
    if phones:
        return phones[0]
    return None


DOMAIN_OR_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,})(?::\d+)?(?:/[^\s]*)?$",
    re.IGNORECASE,
)

IGNORED_FILE_EXTENSIONS = (
    ".json", ".xml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf",
    ".css", ".js", ".ico", ".webmanifest",
)


def is_domain_or_url(text: Optional[str]) -> bool:
    """
    Check if a text string is a domain or URL rather than a company name.
    Examples: 'tripeaztaxipartners.com', 'Www.Aarviengineering.in', 'https://example.com'
    Preserves modern company names like 'Gnani.ai' or 'Apolitical.co'.
    """
    if not text:
        return False
    cleaned = str(text).strip()
    if not cleaned or " " in cleaned or "@" in cleaned:
        return False
    if cleaned.lower().endswith(IGNORED_FILE_EXTENSIONS):
        return False
    if re.match(r"^(?:https?://|www\.)", cleaned, re.IGNORECASE):
        return True
    if "/" in cleaned and "." in cleaned:
        return True
    cleaned_lower = cleaned.lower()
    # Brand company names like Gnani.ai or Apolitical.co (capitalized, no www, no path)
    if any(cleaned_lower.endswith(t) for t in [".ai", ".co"]):
        if not cleaned_lower.startswith("www.") and "/" not in cleaned and any(c.isupper() for c in cleaned):
            return False
    return bool(DOMAIN_OR_URL_PATTERN.match(cleaned))


def normalize_website(url: Optional[str]) -> Optional[str]:
    """
    Normalize website URLs generically:
    - Www.Aarviengineering.in -> https://www.aarviengineering.in
    - www.example.com -> https://www.example.com
    - example.com -> https://example.com
    - https://example.com/ -> https://example.com
    - http://example.com/ -> http://example.com
    Validates that the input actually looks like a domain/URL.
    Does NOT blindly prepend https:// to arbitrary text.
    """
    if not url:
        return None
    cleaned = normalize_string(url)
    if not cleaned:
        return None
    cleaned = cleaned.strip("\"'`*.,;")
    if not cleaned:
        return None

    # Handle comma/semicolon/pipe/space-delimited multiple URLs (e.g. 'https://example.com , www.second.com')
    if any(sep in cleaned for sep in [",", ";", "|"]) or (" " in cleaned and any(k in cleaned.lower() for k in ["http", ".com", ".in", ".org", "www."])):
        parts = [p.strip("\"'`*.,; ") for p in re.split(r"[,;|]+|\s+", cleaned) if p.strip()]
        for part in parts:
            if part and part != cleaned:
                part_norm = normalize_website(part)
                if part_norm:
                    return part_norm
        return None

    # Handle URLs with embedded email / auth prefix (e.g. 'https://info@twin-ringwire.com')
    if "@" in cleaned:
        if cleaned.lower().startswith(("http://", "https://")):
            domain_part = cleaned.split("@")[-1].strip("/ ")
            if domain_part and domain_part != cleaned:
                return normalize_website(domain_part)
        return None

    if not cleaned or " " in cleaned or "@" in cleaned:
        return None
    if cleaned.lower() in {"none", "null", "undefined", "n/a", "unknown", "website", "url"}:
        return None
    if cleaned.lower().endswith(IGNORED_FILE_EXTENSIONS):
        return None
    if not DOMAIN_OR_URL_PATTERN.match(cleaned):
        return None

    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = "https://" + cleaned

    try:
        parsed = urlparse(cleaned)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        netloc = parsed.netloc.lower()
        if not netloc or "." not in netloc:
            return None

        path = parsed.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        elif path == "/":
            path = ""

        normalized = urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            "",  # strip fragment
        ))
        return normalized
    except Exception:
        return None


def normalize_record(record: ExhibitorRecord) -> ExhibitorRecord:
    """Normalize all fields of an ExhibitorRecord."""
    company_name = normalize_company_name(record.company_name)
    website = normalize_website(record.website) if record.website else None

    # If company_name was inadvertently passed as a domain and website is empty, save website
    if is_domain_or_url(company_name) and not website:
        website = normalize_website(company_name)

    source_url = normalize_url(record.source_url) or record.source_url
    detail_url = (
        normalize_url(record.detail_url, base_url=source_url)
        if record.detail_url
        else None
    )

    country = normalize_string(record.country)
    city = normalize_string(record.city)
    state = normalize_string(record.state)
    address = normalize_string(record.address)

    # Infer country / state / city for India if not already set
    if not country:
        if state and state.strip().lower() in INDIAN_STATES:
            country = "India"
        elif address:
            addr_lower = address.lower()
            for st in INDIAN_STATES:
                if re.search(r"\b" + re.escape(st) + r"\b", addr_lower):
                    state = state or st.title()
                    country = "India"
                    if not city and "," in address:
                        cand_city = address.split(",", 1)[0].strip()
                        if (
                            cand_city
                            and len(cand_city) < 40
                            and not any(k in cand_city.lower() for k in ["floor", "plot", "street", "road", "block", "sector", "near", "opp"])
                        ):
                            city = cand_city.title()
                    break
        if not country:
            # Check if any phone starts with +91 or 91
            all_raw_p = list(record.phones) + ([record.phone] if record.phone else [])
            for p_item in all_raw_p:
                p_str = str(p_item).strip()
                if p_str.startswith(("+91", "0091")):
                    country = "India"
                    break
        if not country and address:
            COMMON_COUNTRY_NAMES = {
                "united states": "United States", "usa": "United States", "u.s.a.": "United States",
                "india": "India", "germany": "Germany", "italy": "Italy", "france": "France",
                "united kingdom": "United Kingdom", "uk": "United Kingdom", "spain": "Spain",
                "japan": "Japan", "china": "China", "south korea": "South Korea", "korea": "South Korea",
                "korea, republic of": "South Korea", "belgium": "Belgium", "switzerland": "Switzerland",
                "netherlands": "Netherlands", "canada": "Canada", "australia": "Australia",
                "brazil": "Brazil", "mexico": "Mexico", "singapore": "Singapore", "taiwan": "Taiwan",
                "austria": "Austria", "sweden": "Sweden", "ireland": "Ireland", "denmark": "Denmark",
                "poland": "Poland", "turkey": "Turkey", "israel": "Israel", "greece": "Greece", "portugal": "Portugal",
            }
            addr_stripped = address.strip().lower()
            if addr_stripped in COMMON_COUNTRY_NAMES:
                country = COMMON_COUNTRY_NAMES[addr_stripped]
            elif "," in address:
                last_part = address.split(",")[-1].strip().lower()
                if last_part in COMMON_COUNTRY_NAMES:
                    country = COMMON_COUNTRY_NAMES[last_part]

    # Extract all distinct valid emails
    raw_emails = list(record.emails)
    if record.email:
        raw_emails.append(record.email)
    emails = extract_all_emails(raw_emails)
    email = normalize_email(emails)

    # Extract all distinct valid phones (enforcing Indian 10 digits standard when applicable)
    raw_phones = list(record.phones)
    if record.phone:
        raw_phones.append(record.phone)
    phones = extract_all_phones(raw_phones, country=country)
    phone = phones[0] if phones else normalize_phone(record.phone, country=country)

    description = normalize_string(record.description)
    booth = normalize_string(record.booth)
    hall = normalize_string(record.hall)
    exhibitor_id = normalize_string(record.exhibitor_id)

    categories = [
        normalize_string(c) for c in record.category if normalize_string(c)
    ]
    products = [
        normalize_string(p) for p in record.products if normalize_string(p)
    ]

    return ExhibitorRecord(
        exhibitor_id=exhibitor_id,
        company_name=company_name,
        description=description,
        website=website,
        email=email,
        emails=emails,
        phone=phone,
        phones=phones,
        address=address,
        country=country,
        city=city,
        state=state,
        booth=booth,
        hall=hall,
        category=[c for c in categories if c],
        products=[p for p in products if p],
        detail_url=detail_url,
        source_url=source_url,
        confidence=record.confidence,
    )


def _get_str_field(record: dict, keys: list[str]) -> Optional[str]:
    # 1. Exact key match
    for k in keys:
        if k in record and record[k] is not None:
            if isinstance(record[k], (dict, list)):
                continue
            v = str(record[k]).strip()
            if v and v.lower() not in {"none", "null", "undefined"}:
                return v

    # 2. Case-insensitive key match (for PascalCase / camelCase API responses)
    lower_map = {str(k).lower(): k for k in record.keys()}
    for k in keys:
        actual_k = lower_map.get(k.lower())
        if actual_k is not None and record[actual_k] is not None:
            if isinstance(record[actual_k], (dict, list)):
                continue
            v = str(record[actual_k]).strip()
            if v and v.lower() not in {"none", "null", "undefined"}:
                return v

    return None


def normalize_api_record(
    record: dict,
    source_url: str,
) -> ExhibitorRecord:

    # Unwrap nested exhibitor, company, customer, client, account dictionary if present
    inner_ex = None
    for wrap_key in ["exhibitor", "company", "customer", "client", "account", "node", "item"]:
        if isinstance(record.get(wrap_key), dict):
            inner_ex = record[wrap_key]
            break

    effective = dict(record)
    if inner_ex:
        for k, v in inner_ex.items():
            if v is not None:
                effective[k] = v

    # Also unwrap nested detail structures (e.g. exhibitorDetail, boothDetail)
    detail_obj = None
    for det_key in ["exhibitorDetail", "exhibitor_detail", "exhibitionDetail", "boothDetail", "booth_detail"]:
        if isinstance(effective.get(det_key), dict):
            detail_obj = effective[det_key]
            break
        elif inner_ex and isinstance(inner_ex.get(det_key), dict):
            detail_obj = inner_ex[det_key]
            break

    if detail_obj:
        for k, v in detail_obj.items():
            if v is not None and k not in effective:
                effective[k] = v

    company_name = _get_str_field(
        effective,
        [
            "company_name",
            "companyName",
            "company",
            "exhibitor_name",
            "exhibitorName",
            "exhibitor",
            "name",
            "title",
        ],
    ) or "Unknown"

    raw_category = (
        effective.get("category")
        or effective.get("categories")
        or effective.get("type")
        or effective.get("sector")
        or effective.get("industry")
        or effective.get("productCategories")
        or effective.get("product_categories")
        or effective.get("segments")
        or effective.get("segment")
    )
    categories = []
    if isinstance(raw_category, list):
        for c in raw_category:
            if isinstance(c, dict):
                c_name = _get_str_field(c, ["name", "label", "title", "category"])
                if c_name:
                    categories.append(c_name)
            elif str(c).strip():
                categories.append(str(c).strip())
    elif raw_category:
        cat_str = str(raw_category).strip()
        if cat_str and cat_str.lower() not in {"none", "null", "undefined"}:
            if "," in cat_str:
                categories = [c.strip() for c in cat_str.split(",") if c.strip()]
            else:
                categories = [cat_str]

    raw_products = effective.get("products") or effective.get("product_list")
    products = []
    if isinstance(raw_products, list):
        for p in raw_products:
            if isinstance(p, dict):
                p_name = _get_str_field(p, ["name", "title", "product"])
                if p_name:
                    products.append(p_name)
            elif p:
                products.append(str(p))
    elif raw_products:
        prod_str = str(raw_products).strip()
        if prod_str and prod_str.lower() not in {"none", "null", "undefined"}:
            products = [prod_str]

    urn_val = _get_str_field(effective, ["urn", "URN", "exhibitor_id", "exhibitorId"])
    ex_id = str(effective["id"]) if effective.get("id") is not None else urn_val

    detail_url = _get_str_field(
        effective,
        ["detail_url", "detailUrl", "profile_url", "profileUrl"],
    )
    if not detail_url and urn_val and source_url:
        base_src = source_url.split("?")[0]
        if "/api/" in base_src:
            detail_url = f"{base_src}?urn={urn_val}"

    # Generate detail URL from slug/rewriteId if available
    rewrite_id = _get_str_field(effective, ["rewriteId", "rewrite_id", "slug"])
    if not detail_url and rewrite_id and source_url:
        base_src = source_url.split("?")[0]
        if "exhibitor-search.html" in base_src:
            detail_url = base_src.replace("exhibitor-search.html", f"exhibitor-search.detail.html/{rewrite_id}.html")
        elif "/exhibitors" in base_src:
            detail_url = f"{base_src}/{rewrite_id}"

    # Generate detail URL for MMIConnect (e.g. mmiconnect.in/app/catalogue/exhibitors/{event_slug})
    user_id = _get_str_field(effective, ["userId", "userid", "user_id"])
    show_id = effective.get("showId") or effective.get("show_id")
    if not detail_url and user_id and source_url and "mmiconnect" in source_url.lower():
        parsed_src = urlparse(source_url)
        slug_match = re.search(r"/catalogue/(?:exhibitors|home|products)/([^/?#]+)", parsed_src.path, re.IGNORECASE)
        event_slug = slug_match.group(1) if slug_match else "ifat2026"
        sid = show_id if show_id is not None else 115
        detail_url = f"{parsed_src.scheme}://{parsed_src.netloc}/app/catalogue/exhibitor-detail/{event_slug}/{sid}/{user_id}"

    # Hall & Stand / Booth
    hall = _get_str_field(
        effective,
        [
            "hall",
            "hallNumber",
            "hall_number",
            "hall_no",
            "hall_name",
            "hallName",
            "hall_num",
            "hallno",
            "hallNo",
            "exhibitorTier",
            "exhibitor_tier",
            "hallTier",
        ],
    )
    booth = _get_str_field(
        effective,
        [
            "booth",
            "boothNumber",
            "booth_number",
            "booth_no",
            "boothno",
            "boothNo",
            "booths",
            "stall",
            "stall_no",
            "stallNumber",
            "stall_number",
            "stall_num",
            "stallno",
            "stallNo",
            "stallName",
            "stall_name",
            "stallCompanyName",
            "stalls",
            "stand",
            "standNumber",
            "stand_number",
            "stand_no",
            "standno",
            "standNo",
            "stands",
            "room_no",
            "room",
        ],
    )

    if not hall or not booth:
        halls_data = None
        if inner_ex and isinstance(inner_ex.get("exhibition"), dict):
            halls_data = inner_ex["exhibition"].get("exhibitionHall")
        if not halls_data:
            halls_data = effective.get("exhibitionHall") or effective.get("halls") or effective.get("locations")
        if isinstance(halls_data, list) and halls_data:
            h0 = halls_data[0]
            if isinstance(h0, dict):
                hall = hall or _get_str_field(h0, ["hall", "hallNumber", "hall_number", "name", "id"])
                stands = h0.get("stand")
                if isinstance(stands, list) and stands and isinstance(stands[0], dict):
                    booth = booth or _get_str_field(stands[0], ["name", "stand", "booth", "id"])
                else:
                    booth = booth or _get_str_field(h0, ["stand", "booth", "stall", "number", "id"])

    # Address / Country / City / Phone / Email / Website
    addr_obj = effective.get("address")
    city = _get_str_field(effective, ["city", "town"])
    country = _get_str_field(effective, ["country", "nation"])
    state = _get_str_field(effective, ["state", "province"])
    address = _get_str_field(
        effective,
        [
            "address",
            "full_address",
            "fullAddress",
            "company_address",
            "companyAddress",
            "street",
            "street_address",
            "streetAddress",
            "location_address",
            "locationAddress",
            "office_address",
            "officeAddress",
        ],
    )
    email = _get_str_field(
        effective,
        ["email", "e-mail", "mail", "companyEmail", "company_email", "contact_email", "contactEmail"],
    )
    phone = _get_str_field(
        effective,
        ["phone", "telephone", "mobile", "contact_phone", "contactPhone", "contact", "mobile_number", "tel"],
    )
    website = _get_str_field(
        effective,
        ["website", "webSite", "url", "link", "homepage", "href"],
    )

    if isinstance(addr_obj, dict):
        city = city or _get_str_field(addr_obj, ["city", "locality"])
        state = state or _get_str_field(addr_obj, ["state", "province"])
        c_val = addr_obj.get("country")
        if isinstance(c_val, dict):
            country = country or _get_str_field(c_val, ["label", "name", "id"])
        elif isinstance(c_val, str):
            country = country or c_val
        address = address or _get_str_field(addr_obj, ["street", "address", "formatedAddress"])
        email = email or _get_str_field(addr_obj, ["email", "mail"])
        phone = phone or _get_str_field(addr_obj, ["tel", "phone", "mobile"])

    # Description
    description = None
    desc_val = effective.get("description")
    if isinstance(desc_val, dict):
        description = _get_str_field(desc_val, ["text", "description", "value"])
    elif isinstance(desc_val, str):
        description = desc_val
    if not description:
        description = _get_str_field(
            effective,
            [
                "shortDescription", "about", "about_the_company", "about_company",
                "companyBio", "company_bio", "bio",
                "overseas_profile", "overseasProfile", "company_profile", "companyProfile", "profile",
            ],
        )

    # Extract contacts and website from description if not directly provided
    extracted_emails = extract_all_emails(
        effective.get("emails")
        or effective.get("email_list")
        or email
    )
    if not extracted_emails and description:
        extracted_emails = extract_all_emails(description)
    if not email and extracted_emails:
        email = extracted_emails[0]

    extracted_phones = extract_all_phones(
        effective.get("phones")
        or effective.get("phone_list")
        or phone,
        country=country,
    )
    if not extracted_phones and description:
        extracted_phones = extract_all_phones(description, country=country)
    if not phone and extracted_phones:
        phone = extracted_phones[0]

    if not website and description:
        url_matches = re.findall(
            r"(?:https?://|www\.)[^\s,;<>]+|[a-zA-Z0-9-]+\.(?:com|in|org|net|co|io|co\.in)[^\s,;/<>]*",
            description,
            re.IGNORECASE,
        )
        for u in url_matches:
            norm_w = normalize_website(u)
            if norm_w and "." in norm_w and not any(k in norm_w for k in ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"]):
                website = norm_w
                break

    unnormalized = ExhibitorRecord(
        exhibitor_id=ex_id,
        company_name=company_name,
        description=description,
        website=website,
        email=email,
        emails=extracted_emails,
        phone=phone,
        phones=extracted_phones,
        address=address,
        country=country,
        city=city,
        state=state,
        booth=booth,
        hall=hall,
        category=categories,
        products=products,
        detail_url=detail_url,
        source_url=source_url,
        confidence=0.9,
    )

    return normalize_record(unnormalized)
