import json
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

from app.models.exhibitor import ExhibitorRecord
from app.extraction.normalizer import (
    normalize_record,
    normalize_api_record,
    normalize_company_name,
    normalize_website,
    is_domain_or_url,
    extract_all_emails,
    extract_all_phones,
)
from app.extraction.api_records import extract_api_records
from app.discovery.repeated_elements import find_repeated_elements


HALL_REGEX = re.compile(
    r"(?i)\b(?:hall|pavilion|hangar)\b\s*(?:no\.?|number|#)?\s*[:\-–]?\s*([A-Za-z0-9\-/]+)"
)
BOOTH_REGEX = re.compile(
    r"(?i)\b(?:stall|booth|stand|room)\b\s*(?:no\.?|number|#)?\s*[:\-–]?\s*([A-Za-z0-9\-/]+)"
)
HALL_BOOTH_COMBO_REGEX = re.compile(
    r"(?i)\bhall\s*([A-Za-z0-9\-/]+)\s*[/|]\s*([A-Za-z0-9\-/]+)"
)

ADDRESS_REGEX = re.compile(
    r"(?i)\b(?:address|registered\s+office|head\s+office|office)\s*[:\-–]\s*([^\n\r<]+)"
)
DESC_REGEX = re.compile(
    r"(?i)\b(?:company\s+description|description|about\s+the\s+company|about\s+company|about\s+us|profile)\s*[:\-–]\s*([^\n\r<]+)"
)
WEBSITE_LABEL_REGEX = re.compile(
    r"(?i)\b(?:website|web|site|url)\s*[:\-–]\s*([^\s<\"'>]+)"
)

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
)
PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,4}[-.\s]?)?(?:\(?\d{2,5}\)?[-.\s]?)?\d{3,5}[-.\s]?\d{3,5}"
)

INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "delhi", "new delhi", "chandigarh", "jammu and kashmir", "ladakh", "puducherry",
}

DISALLOWED_BOOTH_PRECEDING_WORDS = {
    "candle", "night", "tv", "coat", "plant", "bed", "bedroom", "living",
    "dining", "bath", "bathroom", "rest", "restroom", "fitting", "show",
    "showroom", "conference", "chat",
}

NON_BOOTH_WORDS = {
    "details", "detail", "info", "information", "no", "number", "location", "space",
    "furniture", "agate", "decor", "decorative", "accessories", "hardware", "lighting",
    "handicrafts", "textiles", "carpets", "garments", "jewellery", "tableware",
    "wares", "products", "category", "categories", "description", "profile",
    "name", "company", "exhibitor", "address", "city", "state", "country", "india",
    "available", "retail", "wholesale", "export", "exporter", "exporters",
}

GLOBAL_LAYOUT_TAGS = {"header", "footer", "nav", "aside"}
GLOBAL_LAYOUT_CLASS_KEYWORDS = [
    "site-footer", "site-header", "page-footer", "page-header",
    "main-footer", "main-header", "tg-footer", "tg-header",
    "footercolumn", "footer-widget", "topmenu", "topbar", "top-bar",
    "contactinfo-box", "about-organizer", "organizer-contact",
    "offcanvas", "off-canvas", "drawer", "sidebar-menu", "mobile-menu",
    "tree-filter", "tree-filters", "tree-wrapper", "filtercontainer", "filter-container",
    "sidebar-filter", "filter-sidebar", "filter-panel", "filters-panel",
    "facet-panel", "facets-panel", "facet-sidebar", "aside__aside",
]


def _is_inside_global_layout(el) -> bool:
    for p in el.parents:
        if p.name in ("html", "body", "[document]"):
            continue
        if p.name in GLOBAL_LAYOUT_TAGS:
            p_cls = " ".join(p.get("class", [])).lower()
            if any(k in p_cls for k in ["card-footer", "card-header", "ex-card-footer", "footer-label", "footer-side"]):
                continue
            return True
        p_cls = " ".join(p.get("class", [])).lower()
        if "row-offcanvas" in p_cls:
            continue
        p_id = str(p.get("id", "")).lower()
        combined = f"{p_cls} {p_id}"
        if any(kw in combined for kw in GLOBAL_LAYOUT_CLASS_KEYWORDS):
            if not any(res in combined for res in ["result", "filtered", "exhibitor", "company"]):
                return True
    return False

URL_LIKE_REGEX = re.compile(
    r"^(?:https?://|www\.)[a-zA-Z0-9\-.]+(?:/.*)?$",
    re.IGNORECASE
)
PLAIN_DOMAIN_REGEX = re.compile(
    r"^[a-z0-9\-]+\.(?:com|org|net|gov|edu|mil|int|info|biz)(?:/.*)?$"
)
DOMAIN_REGEX = re.compile(
    r"^(?:https?://)?(?:www\.)?[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*(?:\.[a-zA-Z]{2,})(?:/.*)?$"
)

SOCIAL_REGEX = re.compile(
    r"^(?:facebook|twitter|linkedin|instagram|youtube|pinterest|tiktok|snapchat|x|whatsapp|line|wechat|telegram|signal|xing|reddit|threads|tumblr)(?:-[a-z0-9]+)?$",
    re.IGNORECASE
)

BOOTH_ONLY_REGEX = re.compile(
    r"^(?:hall|stall|booth|stand|pavilion)\b\s*(?:no\.?|number|#)?\s*[:\-–]?\s*[A-Za-z0-9\-/]+(?:\s*[/|,\-]\s*(?:hall|stall|booth|stand|pavilion)?\s*(?:no\.?|number|#)?\s*[:\-–]?\s*[A-Za-z0-9\-/]+)*$",
    re.IGNORECASE,
)

COMMON_STOP_WORDS = {
    "your", "our", "my", "or", "and", "the", "in", "on", "at", "by", "for",
    "from", "to", "with", "about", "more", "less", "we", "you", "they",
    "it", "this", "that", "all", "any", "some", "none", "not", "no", "yes",
}

DISALLOWED_EXACT_NAMES = {
    "exhibitor directory", "exhibitors directory", "directory", "exhibitor list",
    "exhibitors list", "list of exhibitors", "total visitors", "total exhibitors",
    "total participants", "total attendees", "the expo", "the exhibition",
    "the event", "the venue", "the conference", "e-badge", "e badge",
    "visitor badge", "download badge", "organizing company", "product categories",
    "product categories on display", "visitor profile", "visitor categories",
    "event agenda", "special events", "terms & conditions", "terms and conditions",
    "privacy policy", "disclaimer", "cookie policy", "expression of interest",
    "email address", "your name", "company name", "phone number", "mobile number",
    "first name", "last name", "full name", "postal code", "zip code", "submit",
    "read more read less", "stay connected", "stay in touch", "book your space now",
    "book your space", "book space now", "receive the latest news", "contact us",
    "quick links", "quick link", "about us", "home",
    "exhibitor", "exhibitors", "exhibit", "exhibitor registration", "exhibitor profile",
    "stall packages", "stall booking", "floor plan", "floorplan", "venue",
    "recommend page", "recommend page by email", "copy to clipboard", "copy link",
    "copy url", "share page", "share this page", "print page", "print this page",
    "add to calendar", "opening times", "opening hours", "themes & events",
    "planning & preparation", "press & media", "press information", "advert",
    "advertisement", "advertising", "sponsored", "texpertise network", "sustainability",
    "exhibitor feedback", "exhibitor enquiry", "exhibitor inquiry",
    "visitor enquiry", "visitor registration", "buyer registration",
    "become an exhibitor", "become a sponsor", "become a partner", "become an attendee",
    "exhibitor profiles", "exhibitor list", "exhibitors list", "exhibitor directory",
    "book your space", "book your stand", "book a pod", "get a pod", "sign up for newsletter",
    "reserve your stand", "reserve your space",
    "[email protected]", "[email protected]", "email protected",
    "amber", "blue", "cyan", "deep-orange", "deep orange", "deep-purple", "deep purple",
    "gray", "grey", "green", "light-blue", "light blue", "light-green", "light green",
    "lime", "orange", "pink", "purple", "red", "teal", "yellow", "indigo", "brown",
    "blue-grey", "blue grey",
}

DISALLOWED_SUBSTRINGS = (
    "©", "copyright", "all rights reserved", "terms & conditions",
    "terms and conditions", "privacy policy", "cookie policy",
    "conclave", "masterclass", "b2b meet", "edition of", "roundtable",
    "panel discussion",
    "[email protected]", "email-protection", "cdn-cgi",
)

DISALLOWED_PREFIXES = (
    "about ", "contact ", "visitor ", "quick links", "list of ", "search ",
    "follow us", "copyright ", "click ", "download ", "organised by", "organized by",
    "sector partner", "supported by", "sort by", "sorted by", "filter by", "filtered by",
    "sort ", "filter ", "show ", "view ", "select ", "order by",
    "book your ", "book a ", "stay connected", "stay in touch", "follow us",
    "connect with", "get in touch", "join us", "subscribe", "receive the ",
    "sign up", "download the ", "download your ", "download our ", "get your ",
    "enter your ", "enter the ", "please enter", "enter email", "enter phone",
    "fill the ", "select your ", "choose your ", "type your ",
    "recommend page", "copy to ", "share this ", "share via ", "print this ",
    "to add companies",
    "become ", "become a ", "become an ",
    "exhibitor feedback", "exhibitor enquiry", "exhibitor inquiry",
    "visitor enquiry", "visitor registration", "buyer registration",
    "reserve your ", "get a pod",
    "[email",
)

GENERIC_NAV_WORDS = {
    "home", "about", "about us", "contact", "contact us", "menu", "search",
    "login", "register", "privacy policy", "terms", "terms of use", "sitemap",
    "view profile", "profile", "read more", "view more", "more info", "website",
    "visit website", "click here", "details", "back", "next", "previous",
}

INVALID_NAME_WORDS = {
    "about", "registration", "visitor", "download", "downloads", "brochure",
    "floor plan", "floorplan", "media", "privacy", "terms", "contact", "login",
    "register", "schedule", "agenda", "venue", "guidelines", "manual",
    "exhibitor manual", "home", "menu", "search", "faq", "faqs", "view profile",
    "read more", "quick links", "links", "list of", "exhibitor list",
    "list of exhibitors", "search stall", "stall list", "stall directory",
    "navigation", "social media", "follow us", "all rights reserved", "copyright",
    "organised by", "organized by", "sector partner", "partner", "partners",
    "sponsor", "sponsors", "media partner", "supported by",
    "sort", "sort by", "sorted by", "filter", "filter by", "filters", "filtered by",
    "clear", "clear all", "reset", "apply", "submit", "select", "order by",
    "view all", "show all", "categories", "product categories",
    "why visit", "why attend", "why exhibit", "who should visit", "who should attend",
    "visitor profile", "exhibitor profile", "opening hours", "event schedule",
    "event dates", "fair dates", "how to reach", "dates and times",
    "advert", "advertisement", "advertising", "sponsored",
}

CORP_ABBR_ENDINGS = (
    "ltd.", "pvt.", "inc.", "corp.", "co.", "llc.", "llp.", "gmbh.",
    "pty.", "plc.", "sa.", "bv.", "srl.", "s.a.", "b.v.", "p.l.c.",
)


def _is_valid_company_name(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 150:
        return False
    name_clean = " ".join(name.strip().split())
    name_lower = name_clean.lower()
    if name_lower in {"unknown", "n/a", "none", "null", "undefined"}:
        return False
    if "[email" in name_lower or "email-protection" in name_lower or "cdn-cgi" in name_lower:
        return False
    if name_lower in GENERIC_NAV_WORDS or name_lower in INVALID_NAME_WORDS:
        return False
    if name_lower in COMMON_STOP_WORDS or name_lower in DISALLOWED_EXACT_NAMES:
        return False
    if any(k in name_lower for k in DISALLOWED_SUBSTRINGS):
        return False
    if any(name_lower.startswith(p) for p in DISALLOWED_PREFIXES):
        return False
    # Reject prompt strings or form field labels ending with colons or question marks
    if name_clean.endswith((":", "：", "?", "ï¼", ";")):
        return False
    # Reject common form field labels / prompts
    if any(k in name_lower for k in [
        "phone number", "telephone", "mobile number", "email address",
        "verification code", "negotiation date", "appointment date",
        "remarks", "captcha", "security code", "your name", "full name",
        "first name", "last name", "password", "user name", "username",
        "company name", "booth number", "stand number", "stall number",
    ]) and not any(name_lower.endswith(abbr) for abbr in CORP_ABBR_ENDINGS):
        return False
    # Reject directory titles, registration/feedback forms, and CTA phrases
    if re.search(r"\b(?:exhibitors?|visitors?|buyers?)\s+(?:feedback|enquiry|enquiries|inquiry|inquiries|registration|portal)\b", name_lower):
        return False
    if re.search(r"\b(?:exhibitors?)\s+(?:list|directory|profiles?|catalog|catalogue)\b", name_lower):
        return False
    if re.search(r"\b(?:list\s+of\s+exhibitors|directory\s+of\s+exhibitors)\b", name_lower):
        return False
    if re.search(r"\bbecome\s+(?:an?\s+)?(?:exhibitor|sponsor|partner|attendee|speaker)\b", name_lower):
        return False
    if re.search(r"\b(?:why\s+exhibit|why\s+visit|why\s+attend)\b", name_lower):
        return False
    if re.search(r"\b(?:book\s+(?:your\s+)?(?:space|stand|stall|pod)|reserve\s+(?:your\s+)?(?:space|stand|stall))\b", name_lower):
        return False
    if re.search(r"\b(?:exhibitors?|companies|brands?)\s*(?:&|and|\+)\s*(?:products?|services?|profiles?)\b", name_lower):
        return False
    if re.search(r"\b(?:take\s+part|participate\s+in|join\s+us|attend\s+the|register\s+now|book\s+now)\b", name_lower):
        return False
    if SOCIAL_REGEX.match(name_clean):
        return False
    if BOOTH_ONLY_REGEX.match(name_clean):
        return False
    if " @ " in name_clean or " presents " in name_lower:
        return False
    # Reject domains or URLs (e.g. tripeaztaxipartners.com, Www.Aarviengineering.in)
    if is_domain_or_url(name_clean):
        return False
    if URL_LIKE_REGEX.match(name_clean):
        return False
    if PLAIN_DOMAIN_REGEX.match(name_clean):
        return False
    if "/" in name_clean and "." in name_clean:
        parts = name_clean.split("/", 1)
        if any(parts[0].endswith(tld) for tld in [".com", ".org", ".net", ".info"]):
            return False
    if EMAIL_REGEX.search(name_clean):
        return False
    # Reject date strings (e.g. '22 October 2026', '23 October 2026')
    if re.search(r"\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}\b", name_lower):
        return False
    # Reject prose sentences (ends with dot or exclamation and has 5+ words, unless ending in corp abbreviation)
    words = name_clean.split()
    is_corp_abbrev = any(name_lower.endswith(abbr) for abbr in CORP_ABBR_ENDINGS)
    if len(words) >= 5 and not is_corp_abbrev and (
        name_clean.endswith((".", "!"))
        or any(phrase in name_lower for phrase in ["to get the", "in your inbox", "check your spam", "click here", "welcome to"])
    ):
        return False
    # Reject phone numbers or digit-heavy strings
    digits = sum(c.isdigit() for c in name_clean)
    if digits >= 5 and (digits / len(name_clean) > 0.4):
        return False
    if digits >= 7:
        return False
    # Reject strings that are purely punctuation or symbols or have no alphabetic letters
    if not any(c.isalpha() for c in name_clean):
        return False
    # Reject physical venue and address strings
    if any(venue_kw in name_lower for venue_kw in [
        "exhibition centre", "exhibition center", "convention centre", "convention center",
        "trade centre", "trade center", "expo centre", "expo center", "parade ground",
        "fair ground", "fair grounds", "nr. isbt",
    ]):
        return False
    if re.search(r"\bsector\s*\d+\b", name_lower):
        return False
    return True


def _extract_from_card(card, source_url: str, base_host: str) -> ExhibitorRecord | None:
    card_cls = (" ".join(card.get("class", [])) + " " + card.get("id", "")).lower()
    if any(k in card_cls for k in ["advertising", "billboard", "adbox", "highlight-text", "lightbox"]):
        return None

    # 1. Company Name: prefer headings, then title/name classes, then clean text
    company_name = None

    # Priority 1: Headings
    for h in card.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        h_text = h.get_text(" ", strip=True)
        if _is_valid_company_name(h_text):
            company_name = h_text
            break

    # Priority 2: Elements with class or ID containing name, title, company, exhibitor, heading
    if not company_name:
        for el in card.find_all(True):
            if el.name in ["script", "style", "table", "tbody", "tr"]:
                continue
            el_cls = (" ".join(el.get("class", [])) + " " + el.get("id", "")).lower()
            if any(k in el_cls for k in ["name", "title", "company", "exhibitor", "heading"]):
                if any(ign in el_cls for ign in ["btn", "button", "icon"]):
                    continue
                el_text = el.get_text(" ", strip=True)
                if _is_valid_company_name(el_text):
                    company_name = el_text
                    break

    # Priority 3: Non-URL anchor text
    if not company_name:
        candidate_anchors = []
        for a in card.find_all("a", href=True):
            a_text = a.get_text(" ", strip=True)
            if _is_valid_company_name(a_text):
                a_cls = " ".join(a.get("class", []))
                is_avatar = any(k in a_cls for k in ["avatar", "rounded-full", "h-1", "h-2", "w-1", "w-2"]) or (len(a_text) <= 2 and a_text.isupper())
                candidate_anchors.append((a_text, is_avatar, len(a_text)))
        if candidate_anchors:
            candidate_anchors.sort(key=lambda x: (not x[1], x[2]), reverse=True)
            company_name = candidate_anchors[0][0]

    # Priority 4: First non-metadata text string
    if not company_name:
        for line in card.stripped_strings:
            line_str = line.strip()
            if _is_valid_company_name(line_str):
                if (
                    not HALL_REGEX.search(line_str)
                    and not BOOTH_REGEX.search(line_str)
                ):
                    company_name = line_str
                    break

    # Priority 5: Image alt text (common in logo-based exhibitor directories)
    if not company_name:
        for img in card.find_all("img", alt=True):
            alt_text = img["alt"].strip()
            if _is_valid_company_name(alt_text) and alt_text.lower() not in {
                "logo", "image", "photo", "banner", "sponsor", "exhibitor",
                "media partner", "partner", "icon", "avatar", "placeholder",
            }:
                company_name = alt_text
                break

    if not company_name or not _is_valid_company_name(company_name):
        return None

    # 2. Hall and Stall / Booth from structured elements, then card text
    card_text = card.get_text(" ", strip=True)
    hall = None
    booth = None

    # Priority 2a: Structured card footer or dedicated label elements
    for f_el in card.find_all(True):
        if f_el.name in ["script", "style", "table"]:
            continue
        el_cls = (" ".join(f_el.get("class", [])) + " " + f_el.get("id", "")).lower()
        if not any(k in el_cls for k in ["footer", "stand", "booth", "stall", "hall", "pavilion", "location"]):
            continue
        f_text = f_el.get_text(" ", strip=True)
        if not hall:
            hm = HALL_REGEX.search(f_text)
            if hm and hm.group(1).lower() not in {"details", "detail", "info", "information", "no", "number", "location"}:
                hall = hm.group(1).strip()
            elif any(k in el_cls for k in ["hall", "pavilion"]) and not any(k in f_text.lower() for k in ["stall", "booth", "stand", "table"]):
                cleaned_h = re.sub(r"(?i)^\s*(?:hall|pavilion|no\.?|#)\s*[:\-–]?\s*", "", f_text).strip()
                if cleaned_h and len(cleaned_h) <= 25 and cleaned_h.lower() not in {"details", "detail", "info", "information", "location"}:
                    hall = cleaned_h
        if not booth:
            bm = BOOTH_REGEX.search(f_text)
            if bm and bm.group(1).lower() not in {"details", "detail", "info", "information", "no", "number", "location", "space"}:
                booth = bm.group(1).strip()
            elif any(k in el_cls for k in ["stand", "booth", "stall"]) and not any(k in f_text.lower() for k in ["hall", "pavilion"]):
                cleaned_b = re.sub(r"(?i)^\s*(?:stand|booth|stall|no\.?|#)\s*[:\-–]?\s*", "", f_text).strip()
                if cleaned_b and len(cleaned_b) <= 25 and cleaned_b.lower() not in NON_BOOTH_WORDS:
                    booth = cleaned_b

    # Priority 2b: Regex on card text
    if not hall:
        for hm in HALL_REGEX.finditer(card_text):
            h_cand = hm.group(1).strip()
            if h_cand.lower() not in {"details", "detail", "info", "information", "no", "number", "location"}:
                hall = h_cand
                break

    if not booth:
        for bm in BOOTH_REGEX.finditer(card_text):
            start = bm.start()
            prefix = card_text[:start].strip().split()
            if prefix and prefix[-1].lower() in DISALLOWED_BOOTH_PRECEDING_WORDS:
                continue
            b_cand = bm.group(1).strip()
            b_lower = b_cand.lower()
            if b_lower in NON_BOOTH_WORDS or b_lower in {"details", "detail", "info", "information", "no", "number", "location", "space"}:
                continue
            if b_cand.isalpha() and len(b_cand) >= 4:
                matched_str = bm.group(0)
                if not any(sep in matched_str.lower() for sep in [":", "#", "no.", "no ", "number"]):
                    continue
            booth = b_cand
            break

    # Check for combined "HALL 1GF / 1G-A4" pattern
    if not hall or not booth:
        combo = HALL_BOOTH_COMBO_REGEX.search(card_text)
        if combo:
            hall = hall or combo.group(1).strip()
            booth = booth or combo.group(2).strip()

    # If hall or booth still missing, check immediate parent container if card is a partial section
    if (not hall or not booth) and card.parent:
        parent = card.parent
        if parent.name in ["div", "li", "section"] and len(parent.find_all(["article", "tr"])) <= 1:
            parent_text = parent.get_text(" ", strip=True)
            if not hall:
                hm = HALL_REGEX.search(parent_text)
                if hm:
                    hall = hm.group(1).strip()
            if not booth:
                bm = BOOTH_REGEX.search(parent_text)
                if bm:
                    booth = bm.group(1).strip()
            if not hall or not booth:
                combo = HALL_BOOTH_COMBO_REGEX.search(parent_text)
                if combo:
                    hall = hall or combo.group(1).strip()
                    booth = booth or combo.group(2).strip()

    # Check location element if hall or booth missing (e.g. location-text "12.1, F03")
    if not hall or not booth:
        loc_el = card.select_one("[class*='location-text'], [class*='location']")
        if loc_el:
            loc_t = loc_el.get_text(" ", strip=True)
            if "," in loc_t:
                parts = [p.strip() for p in loc_t.split(",", 1)]
                if parts[0] and not hall:
                    hall = parts[0]
                if len(parts) > 1 and parts[1] and not booth:
                    booth = parts[1]

    # 3. Location, City, State, Country, Categories from structured rows
    city = None
    state = None
    country = None
    address = None
    categories = []

    # Priority 3a: Extract from structured info rows or icon rows
    for row in card.select(".ex-info-row, [class*='info-row'], [class*='contact-row'], [class*='detail-row']"):
        row_text = row.get_text(" ", strip=True)
        icons = [cls for i_el in row.find_all(["i", "svg", "span"]) for cls in i_el.get("class", [])]

        # Location / Address
        if any(ic in icons for ic in ["fa-location-dot", "fa-map-marker", "fa-map-pin", "fa-location-arrow", "fa-city"]) or "location" in row_text.lower():
            if not address:
                address = row_text
            if "," in row_text:
                parts = [p.strip() for p in row_text.split(",", 1)]
                city = city or parts[0]
                cand_st = parts[1].strip()
                if cand_st.lower() in INDIAN_STATES:
                    state = state or cand_st
                    country = country or "India"
            elif row_text.lower() in INDIAN_STATES:
                state = state or row_text
                country = country or "India"

        # Categories from info row
        if any(ic in icons for ic in ["fa-layer-group", "fa-boxes", "fa-tags", "fa-tag"]) or "product category:" in row_text.lower() or "category:" in row_text.lower():
            cat_text = re.sub(r"(?i)^.*(?:product\s+category|category)\s*:\s*", "", row_text).strip()
            if cat_text:
                for c in re.split(r"[,;/]+", cat_text):
                    c_clean = c.strip()
                    if c_clean and c_clean not in categories:
                        categories.append(c_clean)

    # Priority 3b: Categories from tags / badges / type
    for tag_el in card.find_all(True):
        if tag_el.name in ["script", "style", "table"]:
            continue
        t_cls = (" ".join(tag_el.get("class", [])) + " " + tag_el.get("id", "")).lower()
        if not any(k in t_cls for k in ["ex-tag", "product-tag", "category-tag", "grid__type", "grid__category", "company-type", "exhibitor-type"]):
            continue
        if any(toggle_cls in t_cls for toggle_cls in ["ex-tag-toggle", "toggle"]):
            continue
        t_text = tag_el.get_text(" ", strip=True)
        if (
            t_text
            and not t_text.startswith("+")
            and not any(w in t_text.lower() for w in ["show more", "show less", "more", "less"])
            and t_text not in categories
            and t_text != company_name
            and t_text != booth
            and len(t_text) <= 50
        ):
            categories.append(t_text)

    # 4. Website, Detail URL, Email, Phone
    website = None
    detail_url = None
    email = None
    emails = []
    phone = None
    phones = []

    card_anchors = list(card.find_all("a", href=True))
    if card.name == "a" and card.get("href"):
        card_anchors.append(card)
    parent_a = card.find_parent("a", href=True)
    if parent_a:
        card_anchors.append(parent_a)

    for a in card_anchors:
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        if _is_inside_global_layout(a):
            continue

        if href.lower().startswith("mailto:"):
            for em in extract_all_emails(href):
                if em not in emails:
                    emails.append(em)
            if not email and emails:
                email = emails[0]
            continue

        if href.lower().startswith("tel:"):
            for ph in extract_all_phones(href, country=country):
                if ph not in phones:
                    phones.append(ph)
            if not phone and phones:
                phone = phones[0]
            continue

        if any(ign in href.lower() for ign in ["cdn-cgi", "email-protection"]):
            continue

        full_url = urljoin(source_url, href)
        parsed_url = urlparse(full_url)
        link_host = parsed_url.netloc.lower()
        a_text = a.get_text(" ", strip=True)

        if "[email" in a_text.lower():
            continue

        is_external = bool(link_host and base_host and link_host != base_host)

        if is_external:
            if not website:
                website = full_url
        else:
            norm_a_text = normalize_company_name(a_text) if a_text else ""
            norm_c_name = normalize_company_name(company_name) if company_name else ""
            if (
                any(k in full_url.lower() for k in ["/co/", "/co?", "exhibitor", "company", "profile", "stand", "booth", "detail"])
                or any(k in a_text.lower() for k in ["profile", "view", "detail", "more"])
                or (norm_c_name and norm_a_text and norm_c_name == norm_a_text)
            ):
                if not detail_url:
                    detail_url = full_url
            elif DOMAIN_REGEX.match(a_text) and not website:
                website = full_url

    # Fallback text website, email, and phone from card text
    if not website:
        web_label_m = re.search(
            r"(?i)\b(?:website|web|url|site)\s*[:\-–]?\s*([a-zA-Z0-9\-.]+\.[a-zA-Z]{2,}(?:/[^\s<\"']*)?)",
            card_text,
        )
        if web_label_m:
            cand_web = web_label_m.group(1).strip()
            if is_domain_or_url(cand_web):
                website = cand_web

    if not website:
        for el in card.find_all(True):
            if el.name in ["script", "style", "table"]:
                continue
            el_cls = (" ".join(el.get("class", [])) + " " + el.get("id", "")).lower()
            if any(k in el_cls for k in ["website", "web-box", "web-link", "site-link"]):
                el_t = el.get_text(" ", strip=True)
                el_t = re.sub(r"(?i)^\s*(?:website|web|url|site)\s*[:\-–]?\s*", "", el_t).strip()
                if is_domain_or_url(el_t):
                    website = el_t
                    break

    # Fallback address from card text or elements
    if not address:
        addr_m = ADDRESS_REGEX.search(card_text)
        if addr_m:
            cand_addr = addr_m.group(1).strip()
            cand_addr = re.sub(r"(?i)\s+(?:booth|hall|stall|website|web|phone|email)\b.*$", "", cand_addr).strip()
            if len(cand_addr) >= 5:
                address = cand_addr

    if not address:
        for el in card.find_all(True):
            if el.name in ["script", "style", "table"]:
                continue
            el_cls = (" ".join(el.get("class", [])) + " " + el.get("id", "")).lower()
            if any(k in el_cls for k in ["address", "location-address", "company-address", "postal"]):
                el_t = el.get_text(" ", strip=True)
                el_t = re.sub(r"(?i)^\s*address\s*[:\-–]\s*", "", el_t).strip()
                if el_t and len(el_t) >= 5 and not el_t.lower().startswith(("phone", "email", "website", "hall", "stall")):
                    address = el_t
                    break

    # Fallback description from card text or elements
    description = None
    desc_m = DESC_REGEX.search(card_text)
    if desc_m:
        cand_desc = desc_m.group(1).strip()
        if len(cand_desc) >= 10:
            description = cand_desc

    if not description:
        for el in card.find_all(True):
            if el.name in ["script", "style", "table"]:
                continue
            el_cls = (" ".join(el.get("class", [])) + " " + el.get("id", "")).lower()
            if any(k in el_cls for k in ["company-description", "company-desc", "company-bio", "about-us", "-item__copy", "result-item__copy"]):
                el_t = el.get_text(" ", strip=True)
                el_t = re.sub(r"(?i)^\s*(?:company\s+description|description|about\s+us)\s*[:\-–]\s*", "", el_t).strip()
                if el_t and len(el_t) >= 15:
                    description = el_t
                    break

    for em in extract_all_emails(card_text):
        if em not in emails:
            emails.append(em)
    if not email and emails:
        email = emails[0]

    for ph in extract_all_phones(card_text, country=country):
        if ph not in phones:
            phones.append(ph)
    if not phone and phones:
        phone = phones[0]

    if booth:
        booth_clean = booth.strip().lower()
        if booth_clean in {
            "ing", "and", "the", "to", "in", "at", "of", "a", "an", "is",
            "are", "as", "by", "for", "with", "from", "on", "no", "not",
        }:
            booth = None
    if hall:
        hall_clean = hall.strip().lower()
        if hall_clean in {
            "ing", "and", "the", "to", "in", "at", "of", "a", "an", "is",
            "are", "as", "by", "for", "with", "from", "on", "no", "not",
        }:
            hall = None

    raw_record = ExhibitorRecord(
        company_name=company_name,
        hall=hall,
        booth=booth,
        website=website,
        address=address,
        country=country,
        city=city,
        state=state,
        category=categories,
        description=description,
        detail_url=detail_url,
        email=email,
        emails=emails,
        phone=phone,
        phones=phones,
        source_url=source_url,
        confidence=0.85,
    )
    return normalize_record(raw_record)


def _extract_from_tables(
    soup: BeautifulSoup,
    source_url: str,
    exclude_containers: list | None = None,
) -> list[ExhibitorRecord]:
    records = []
    last_table_schema = None
    exclude_container_ids = {id(c) for c in exclude_containers} if exclude_containers else set()

    for table in soup.find_all("table"):
        if exclude_container_ids and any(id(p) in exclude_container_ids for p in table.parents):
            continue
        # Skip form tables (tables used for input forms / contact forms)
        if table.find_parent("form") is not None:
            continue
        if len(table.find_all(["input", "textarea", "select"])) >= 2:
            continue
        all_table_rows = table.find_all("tr")
        if not all_table_rows:
            continue

        best_header_idx = None
        best_schema = None
        best_score = 0

        # Scan candidate header rows in the first 5 rows (handling title banners / multi-row headers)
        for r_idx, r_cand in enumerate(all_table_rows[:5]):
            cells = r_cand.find_all(["th", "td"])
            headers = [th.get_text(" ", strip=True).lower() for th in cells]
            if not headers:
                continue

            # If this row is a 1-cell title banner (e.g. <td colspan="3">LIST OF EXHIBITORS</td>)
            # while the table has multiple columns, skip it as a column header
            if len(headers) == 1 and len(all_table_rows) > 1:
                if any(len(next_r.find_all(["th", "td"])) > 1 for next_r in all_table_rows[r_idx + 1:r_idx + 4]):
                    continue

            company_idx = None
            booth_idx = None
            hall_idx = None
            web_idx = None
            email_idx = None
            phone_idx = None
            address_idx = None
            score = 0

            for idx, h in enumerate(headers):
                if any(k in h for k in ["website", "web", "url", "domain"]):
                    if web_idx is None:
                        web_idx = idx
                        score += 2
                elif any(k in h for k in ["company", "exhibitor", "firm", "participant", "name"]):
                    if company_idx is None:
                        company_idx = idx
                        score += 3
                elif any(k in h for k in ["stall", "booth", "stand"]):
                    if booth_idx is None:
                        booth_idx = idx
                        score += 2
                elif any(k in h for k in ["hall", "pavilion", "location", "hangar"]):
                    if hall_idx is None:
                        hall_idx = idx
                        score += 1
                elif any(k in h for k in ["address", "street", "city", "state", "postal"]):
                    if address_idx is None:
                        address_idx = idx
                        score += 1
                elif "email" in h:
                    if email_idx is None:
                        email_idx = idx
                        score += 2
                elif any(k in h for k in ["phone", "mobile", "contact"]):
                    if phone_idx is None:
                        phone_idx = idx
                        score += 1
                elif any(k in h for k in ["s.no", "sr.no", "sl.no", "s no", "serial", "no."]):
                    score += 1

            if any(cell.name == "th" for cell in cells):
                score += 1

            if company_idx is not None and score > best_score:
                best_score = score
                best_header_idx = r_idx
                best_schema = (company_idx, booth_idx, hall_idx, web_idx, email_idx, phone_idx, address_idx)

        if best_header_idx is not None:
            last_table_schema = best_schema
            company_idx, booth_idx, hall_idx, web_idx, email_idx, phone_idx, address_idx = best_schema
            rows = all_table_rows[best_header_idx + 1:]
        elif last_table_schema is not None:
            # Continuation table in paginated/tabbed directory layout
            company_idx, booth_idx, hall_idx, web_idx, email_idx, phone_idx, address_idx = last_table_schema
            rows = all_table_rows
        else:
            # Headerless table detection
            all_table_rows = table.find_all("tr")
            if len(all_table_rows) >= 5:
                sample_rows = all_table_rows[:30]
                col_scores = {}
                for r in sample_rows:
                    cols = r.find_all(["td", "th"])
                    for c_idx, c in enumerate(cols):
                        txt = c.get_text(" ", strip=True)
                        if c_idx not in col_scores:
                            col_scores[c_idx] = {"total": 0, "valid": 0}
                        col_scores[c_idx]["total"] += 1
                        if _is_valid_company_name(txt):
                            col_scores[c_idx]["valid"] += 1
                best_idx = None
                best_ratio = 0.0
                for c_idx, sc in col_scores.items():
                    if sc["total"] >= 3:
                        ratio = sc["valid"] / sc["total"]
                        if ratio >= 0.5 and ratio > best_ratio:
                            best_ratio = ratio
                            best_idx = c_idx
                if best_idx is not None:
                    company_idx = best_idx
                    rows = all_table_rows
                    last_table_schema = (company_idx, booth_idx, hall_idx, web_idx, email_idx, phone_idx, address_idx)
                else:
                    continue
            else:
                continue

        for row in rows:
                cols = row.find_all(["td", "th"])
                if len(cols) <= company_idx:
                    continue
                name = cols[company_idx].get_text(" ", strip=True)
                if not _is_valid_company_name(name):
                    continue

                booth = cols[booth_idx].get_text(" ", strip=True) if booth_idx is not None and len(cols) > booth_idx else None
                hall = cols[hall_idx].get_text(" ", strip=True) if hall_idx is not None and len(cols) > hall_idx else None
                address = cols[address_idx].get_text(" ", strip=True) if address_idx is not None and len(cols) > address_idx else None

                web = None
                if web_idx is not None and len(cols) > web_idx:
                    a = cols[web_idx].find("a", href=True)
                    web = urljoin(source_url, a["href"]) if a else cols[web_idx].get_text(" ", strip=True)

                email = cols[email_idx].get_text(" ", strip=True) if email_idx is not None and len(cols) > email_idx else None
                phone = cols[phone_idx].get_text(" ", strip=True) if phone_idx is not None and len(cols) > phone_idx else None

                detail_url = None
                a_co = cols[company_idx].find("a", href=True)
                if a_co and not _is_inside_global_layout(a_co):
                    href = a_co["href"].strip()
                    if href and not href.startswith(("#", "javascript:")):
                        full_u = urljoin(source_url, href)
                        parsed_u = urlparse(full_u)
                        link_h = parsed_u.netloc.lower()
                        base_h = urlparse(source_url).netloc.lower()
                        if link_h == base_h:
                            detail_url = full_u
                        elif not web:
                            web = full_u

                records.append(normalize_record(ExhibitorRecord(
                    company_name=name,
                    booth=booth,
                    hall=hall,
                    website=web,
                    address=address,
                    email=email,
                    phone=phone,
                    detail_url=detail_url,
                    source_url=source_url,
                    confidence=0.9,
                )))
    return records


def _extract_from_lists(
    soup: BeautifulSoup,
    source_url: str,
    base_host: str,
    exclude_containers: list | None = None,
) -> list[ExhibitorRecord]:
    records: list[ExhibitorRecord] = []
    seen_names = set()
    exclude_container_ids = {id(c) for c in exclude_containers} if exclude_containers else set()

    for list_el in soup.find_all(["ul", "ol"]):
        if _is_inside_global_layout(list_el):
            continue
        if exclude_container_ids and any(id(p) in exclude_container_ids for p in list_el.parents):
            continue
        lis = list_el.find_all("li", recursive=False)
        if len(lis) < 5:
            continue
        valid_lis = []
        for li in lis:
            if _is_inside_global_layout(li):
                continue
            rec = _extract_from_card(li, source_url, base_host)
            if rec and rec.company_name and _is_valid_company_name(rec.company_name):
                valid_lis.append(rec)
        if len(valid_lis) >= 5 and (len(valid_lis) / len(lis) >= 0.45):
            for rec in valid_lis:
                norm_n = rec.company_name.lower()
                if norm_n not in seen_names:
                    seen_names.add(norm_n)
                    records.append(rec)

    return records


def _extract_from_scripts(
    soup: BeautifulSoup,
    source_url: str,
) -> list[ExhibitorRecord]:
    records: list[ExhibitorRecord] = []
    seen_names = set()

    for s in soup.find_all("script"):
        text = s.string or s.get_text()
        if not text:
            continue
        text_clean = text.strip()
        text_lower = text_clean.lower()
        if not (("name" in text_lower or "company" in text_lower or "exhibitor" in text_lower) and (
            "stall" in text_lower or "booth" in text_lower or "stand" in text_lower or "hall" in text_lower
        )):
            continue

        # Strategy 1: Look for JSON structures in script tags (arrays or objects)
        json_candidates = []
        if text_clean.startswith(("[", "{")):
            json_candidates.append(text_clean)

        for m in re.finditer(r"=\s*(\[\s*\{.*?\}\s*\])\s*(?:;|\n|$)", text, re.DOTALL):
            json_candidates.append(m.group(1))

        for m in re.finditer(r"=\s*(\{\s*[\"'].*?\}\s*)\s*(?:;|\n|$)", text, re.DOTALL):
            json_candidates.append(m.group(1))

        script_extracted = False
        for cand in json_candidates:
            try:
                payload = json.loads(cand)
                api_recs = extract_api_records(payload, source_url)
                if api_recs:
                    for rec in api_recs:
                        if rec.company_name and _is_valid_company_name(rec.company_name):
                            name_key = rec.company_name.lower()
                            if name_key not in seen_names:
                                seen_names.add(name_key)
                                records.append(rec)
                    if records:
                        script_extracted = True
            except Exception:
                pass

        if script_extracted:
            continue

        # Strategy 2: Fallback regex extraction for JS object literals
        obj_pattern = re.compile(r"\{\s*(?:[^{}]+?)\s*\}")
        kv_pattern = re.compile(
            r"""(?:["']?([a-zA-Z0-9_-]+)["']?)\s*:\s*(?:["']([^"']*)["']|([0-9]+(?:\.[0-9]+)?)|null|true|false)"""
        )

        for m in obj_pattern.finditer(text):
            block = m.group(0)
            kvs = {}
            for k, val_quoted, val_num in kv_pattern.findall(block):
                val = val_quoted if val_quoted != "" else val_num
                if val:
                    kvs[k] = val
            if not kvs:
                continue

            rec = normalize_api_record(kvs, source_url)
            if not rec.company_name or not _is_valid_company_name(rec.company_name):
                continue
            name_key = rec.company_name.lower()
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
            records.append(rec)

    return records


def _extract_from_links(
    soup: BeautifulSoup,
    source_url: str,
    base_host: str,
) -> list[ExhibitorRecord]:
    records = []
    keywords = [
        "exhibitor",
        "company",
        "supplier",
        "vendor",
        "manufacturer",
        "profile",
        "member",
        "brand",
        "stand",
        "booth",
    ]

    parsed_source = urlparse(source_url)
    source_path_clean = parsed_source.path.rstrip("/").lower()
    source_host = parsed_source.netloc.lower()

    for link in soup.find_all("a", href=True):
        if _is_inside_global_layout(link):
            continue
        href_lower = link["href"].lower()
        if any(ign in href_lower for ign in ["cdn-cgi", "email-protection"]):
            continue
        name = link.get_text(" ", strip=True)
        if not _is_valid_company_name(name):
            continue

        detail_url = urljoin(source_url, link["href"])
        parsed_link = urlparse(detail_url)
        link_host = parsed_link.netloc.lower()
        link_path_clean = parsed_link.path.rstrip("/").lower()

        # Skip self-referential links pointing to current directory page
        if link_host == source_host and link_path_clean == source_path_clean:
            continue

        # Skip generic site navigation paths
        if link_path_clean in {
            "", "/", "/exhibitor", "/exhibitors", "/exhibit", "/register",
            "/registration", "/exhibitor-directory", "/exhibitors-directory",
            "/exhibitor-registration", "/visitor-registration",
            "/organizing-company", "/contact-us", "/about-us",
            "/privacy-policy", "/terms-and-conditions",
        }:
            continue

        # Skip form, feedback, enquiry, and document download paths
        if any(nav in link_path_clean for nav in [
            "enquiry", "inquiry", "feedback", "newsletter", "brochure",
            "floor-plan", "floorplan", "guidelines", "manual",
            "exhibitors-profile", "exhibitor-profile", "exhibitors-list", "exhibitor-list",
        ]):
            continue

        # Check if text or link path contains keywords
        text = f"{name} {parsed_link.path}".lower()
        if not any(k in text for k in keywords):
            continue

        is_external = bool(link_host and base_host and link_host != base_host)

        raw_record = ExhibitorRecord(
            company_name=name,
            detail_url=detail_url if not is_external else None,
            website=detail_url if is_external else None,
            source_url=source_url,
            confidence=0.8,
        )
        records.append(normalize_record(raw_record))

    return records


def extract_html_records(
    html: str,
    source_url: str,
) -> list[ExhibitorRecord]:

    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(source_url).netloc.lower()

    # ------------------------------------------------------------------
    # Strategy 1: Repeated Card / Container Elements
    # ------------------------------------------------------------------
    candidate_containers = []

    # 1a. Semantic articles
    articles = soup.find_all("article")
    if len(articles) >= 2:
        candidate_containers = articles

    # 1b. Common card selectors
    if not candidate_containers:
        for selector in [
            ".exhibitor-card",
            ".exhibitor-item",
            ".ex-card",
            "[class*='exhibitor-search-result-item']",
            "[class*='search-result-item']",
            "[class*='exhibitor-card']",
            "[class*='exhibitor-item']",
            "[class*='company-card']",
            "[class*='company-item']",
            "[class*='directory-item']",
            "[class*='directory-card']",
            "[class*='ex-card']",
            "[class*='card']",
        ]:
            found = soup.select(selector)
            if len(found) >= 2:
                filtered = []
                for el in found:
                    cls = " ".join(el.get("class", [])).lower()
                    if any(k in cls for k in ["advertising", "billboard", "adbox", "highlight-text", "lightbox"]):
                        continue
                    if _is_inside_global_layout(el):
                        continue
                    filtered.append(el)
                if len(filtered) >= 2:
                    candidate_containers = filtered
                    break

    # 1c. Repeated structural elements from discovery
    LAYOUT_CLASSES = {
        "container", "container-fluid", "row", "col", "wrapper", "header",
        "footer", "nav", "navbar", "sidebar", "menu", "page", "main",
        "dropdown", "dropdown-menu", "dropdown-item", "nav-item",
    }
    CMS_LAYOUT_KEYWORDS = {
        "widget", "column", "container", "wrapper", "section", "elementor",
        "builder", "layout", "header", "footer", "sidebar", "menu", "navbar",
        "banner", "hero", "slider", "carousel", "form", "modal", "popup",
        "dialog", "drawer", "accordion", "tab", "row", "col", "grid",
        "e-con", "site", "wp-block", "content", "inner",
        "description", "desc", "summary", "caption", "text", "bio", "excerpt",
        "contentbox", "eventhead", "leftarea", "rightarea",
        "offcanvas", "off-canvas", "drawer", "teaser", "billboard", "advert",
        "advertising", "advertising-billboard",
    }
    EXHIBITOR_KEYWORDS = {
        "exhibitor", "company", "firm", "participant", "supplier", "vendor",
        "stall", "booth", "stand", "directory", "profile", "member",
    }
    NON_CARD_TAGS = {
        "a", "link", "span", "script", "style", "meta", "br", "hr",
        "input", "button", "option", "i", "b", "strong", "em", "small",
        "label", "ul", "ol", "li", "tbody", "thead", "tfoot", "form",
        "td", "th", "tr", "table", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    }
    FILTER_KEYWORDS = {
        "filter", "filters", "tree-filter", "tree-filters", "facet", "facets",
        "refinement", "refinements", "treeitem", "filter-item", "filteritem",
        "filter-option", "filteroption", "facet-item", "facetitem",
    }
    if not candidate_containers:
        repeated = find_repeated_elements(html, minimum_repetitions=3)
        candidate_options = []
        for rep in repeated:
            sig = rep["signature"]
            parts = sig.split(".")
            tag_name = parts[0].lower()
            classes = set(parts[1:])
            if tag_name in NON_CARD_TAGS:
                continue
            # Skip elements whose class is explicitly a filter / facet control
            if any(any(fk in c.lower() for fk in FILTER_KEYWORDS) for c in classes):
                if not any(res in c.lower() for res in ["result", "filtered", "exhibitor", "company"] for c in classes):
                    continue
            has_exhibitor_class = any(
                any(k in c.lower() for k in EXHIBITOR_KEYWORDS)
                for c in classes
            )
            has_layout_class = any(
                any(k in c.lower() for k in CMS_LAYOUT_KEYWORDS)
                or c in LAYOUT_CLASSES
                or c.startswith(("nav", "dropdown", "elementor", "wp-block", "site-", "o-off-canvas"))
                for c in classes
            )
            if "." in sig:
                try:
                    found = [
                        el for el in soup.find_all(tag_name)
                        if classes.issubset(set(el.get("class", [])))
                    ]
                except Exception:
                    continue
                found = [el for el in found if not _is_inside_global_layout(el)]
                if len(found) >= 3:
                    valid_names_count = 0
                    detail_count = 0
                    booth_count = 0
                    web_count = 0
                    for el in found[:15]:
                        card_rec = _extract_from_card(el, source_url, base_host)
                        if card_rec and card_rec.company_name and _is_valid_company_name(card_rec.company_name):
                            valid_names_count += 1
                        if card_rec and card_rec.detail_url:
                            detail_count += 1
                        if card_rec and card_rec.booth:
                            booth_count += 1
                        if card_rec and card_rec.website:
                            web_count += 1
                    # If it was flagged as layout, but is actually an exhibitor card cluster (e.g. grid items)
                    if has_layout_class and not has_exhibitor_class:
                        # Reject if elements contain large nested lists or tables (it's a column/page wrapper, not an individual card)
                        if any(len(el.find_all("li")) > 5 or len(el.find_all("tr")) > 5 for el in found):
                            continue
                        if valid_names_count == 0 or (detail_count == 0 and booth_count == 0 and web_count == 0 and len(found) < 10):
                            continue
                    candidate_options.append({
                        "elements": found,
                        "valid_count": valid_names_count,
                        "detail_count": detail_count,
                        "booth_count": booth_count,
                        "web_count": web_count,
                        "total_count": len(found),
                        "has_exhibitor_class": has_exhibitor_class,
                    })

        if candidate_options:
            candidate_options.sort(
                key=lambda opt: (
                    opt["valid_count"] > 0,
                    opt["detail_count"] > 0,
                    opt["valid_count"],
                    opt["detail_count"],
                    opt["total_count"],
                    opt["has_exhibitor_class"],
                ),
                reverse=True,
            )
            candidate_containers = candidate_options[0]["elements"]

    # Extract records from discovered containers (filtering to top-level containers only)
    records = []
    if candidate_containers:
        container_ids = {id(c) for c in candidate_containers}
        top_containers = [
            c for c in candidate_containers
            if not any(id(p) in container_ids for p in c.parents)
        ]
        for card in top_containers:
            rec = _extract_from_card(card, source_url, base_host)
            if rec:
                records.append(rec)

    # ------------------------------------------------------------------
    # Strategy 2: Directory Table Extraction
    # ------------------------------------------------------------------
    table_recs = _extract_from_tables(soup, source_url, exclude_containers=candidate_containers)
    if table_recs:
        if not records or len(table_recs) > len(records):
            records = table_recs

    # ------------------------------------------------------------------
    # Strategy 2b: Directory List Extraction (ul / ol lists of exhibitors)
    # ------------------------------------------------------------------
    list_recs = _extract_from_lists(soup, source_url, base_host)
    if list_recs:
        if not records or len(list_recs) > len(records):
            records = list_recs
        else:
            existing_names = {r.company_name.lower() for r in records if r.company_name}
            for lr in list_recs:
                if lr.company_name and lr.company_name.lower() not in existing_names:
                    records.append(lr)
                    existing_names.add(lr.company_name.lower())

    # ------------------------------------------------------------------
    # Strategy 3: Inline Script Dataset Extraction
    # ------------------------------------------------------------------
    script_recs = _extract_from_scripts(soup, source_url)
    if script_recs:
        if not records or len(script_recs) > len(records):
            records = script_recs

    # ------------------------------------------------------------------
    # Strategy 4: Fallback to Link-Based Extraction
    # ------------------------------------------------------------------
    if not records:
        records = _extract_from_links(soup, source_url, base_host)

    return records