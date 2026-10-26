import re
import json
from typing import Optional
from urllib.parse import urlparse, urljoin, quote
from bs4 import BeautifulSoup

from app.models.exhibitor import ExhibitorRecord
from app.models.scrape_budget import ScrapeBudget
from app.discovery.http import fetch_url
from app.extraction.normalizer import (
    normalize_website,
    normalize_email,
    normalize_phone,
    extract_all_emails,
    extract_all_phones,
    normalize_string,
    normalize_company_name,
    normalize_record,
    is_domain_or_url,
)


SOCIAL_DOMAINS = {
    "facebook.com", "fb.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "youtube.com", "youtu.be", "pinterest.com",
    "tiktok.com", "whatsapp.com", "wa.me", "t.me", "telegram.org",
    "wechat.com", "reddit.com", "github.com",
}

GENERIC_DOMAINS = {
    "google.com", "maps.google.com", "goo.gl", "apple.com",
    "play.google.com", "microsoft.com", "schema.org", "w3.org",
}

PLATFORM_DOMAINS = {
    "swapcard.com", "mapyourshow.com", "a2zinc.net", "expocad.com", "eventmaker.com",
    "grip.events", "brella.io", "hopin.com", "cvent.com", "pathable.com",
    "reenvision.in", "veblogy.com",
}


def _is_external_company_website(url: str, directory_host: str) -> bool:
    """Validate that a URL is a valid external company website, not social or self-referential."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if not host or "." not in host:
        return False
    if directory_host and (host == directory_host or host.endswith("." + directory_host)):
        return False
    for sd in SOCIAL_DOMAINS:
        if host == sd or host.endswith("." + sd):
            return False
    for gd in GENERIC_DOMAINS:
        if host == gd or host.endswith("." + gd):
            return False
    for pd in PLATFORM_DOMAINS:
        if host == pd or host.endswith("." + pd):
            return False
    if any(k in host for k in ["help-", "support.", "faq."]):
        return False
    return True


GLOBAL_LAYOUT_TAGS = {"header", "footer", "nav", "aside"}
GLOBAL_LAYOUT_CLASS_KEYWORDS = [
    "site-footer", "site-header", "page-footer", "page-header",
    "main-footer", "main-header", "tg-footer", "tg-header",
    "footercolumn", "footer-widget", "header-widget",
    "topmenu", "topbar", "top-bar", "contactinfo-box",
    "copyright", "about-organizer", "organizer-info", "organizer-contact",
]


def _is_global_layout_element(tag) -> bool:
    if not hasattr(tag, "name") or not tag.name or not hasattr(tag, "attrs") or tag.attrs is None:
        return False
    if tag.name in GLOBAL_LAYOUT_TAGS:
        return True
    classes = tag.get("class", [])
    if isinstance(classes, list):
        cls = " ".join(classes).lower()
    else:
        cls = str(classes).lower()
    tag_id = str(tag.get("id", "")).lower()
    combined = f"{cls} {tag_id}"
    return any(kw in combined for kw in GLOBAL_LAYOUT_CLASS_KEYWORDS)


def extract_detail_fields(html_content: str, source_url: str) -> dict:
    """
    Deterministically extract structured exhibitor profile fields from detail page HTML:
    - website
    - address
    - description
    - email
    - phone
    - booth
    - hall
    - products
    - category
    Excludes global page header and footer contact information to prevent assigning
    organizer contact details to individual exhibitors.
    """
    if not html_content:
        return {}

    directory_host = urlparse(source_url).netloc.lower()
    soup = BeautifulSoup(html_content, "html.parser")

    data = {
        "website": None,
        "address": None,
        "city": None,
        "state": None,
        "country": None,
        "description": None,
        "email": None,
        "emails": [],
        "phone": None,
        "phones": [],
        "booth": None,
        "hall": None,
        "products": [],
        "category": [],
    }

    # Handle SPA detail pages on mmiconnect.in via direct GraphQL query
    if "mmiconnect.in/app/catalogue/exhibitor-detail/" in source_url.lower():
        m = re.search(r"/exhibitor-detail/([^/]+)/(\d+)/([a-f0-9-]+)", source_url, re.IGNORECASE)
        if m:
            group, show_id, user_id = m.group(1), int(m.group(2)), m.group(3)
            try:
                q = """query getExibitorDetailsForGroup($userId: String, $showId: Int) {
  globalAnonymousQueries {
    customers(userId: $userId, showId: $showId) {
      id
      companyName
      website
      emailAddress
      telephoneNo
      mobileNo
      city
      country
      address1
      address2
      companyProfile
      exhibitorDetail {
        boothNo
        hallNo
      }
    }
  }
}"""
                resp = fetch_url(
                    "https://mmiconnect.in/graphql",
                    method="POST",
                    json={
                        "operationName": "getExibitorDetailsForGroup",
                        "variables": {"userId": user_id, "showId": show_id},
                        "query": q,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                        "Authorization": "Bearer null",
                    },
                    timeout=10,
                )
                resp_data = resp.json()
                cust = (
                    resp_data.get("data", {})
                    .get("globalAnonymousQueries", {})
                    .get("customers", {})
                )
                if cust:
                    web = cust.get("website")
                    if web and _is_external_company_website(web, directory_host):
                        data["website"] = normalize_website(web)
                    addr_parts = [p.strip() for p in [cust.get("address1"), cust.get("address2"), cust.get("city"), cust.get("country")] if p and str(p).strip()]
                    if addr_parts:
                        data["address"] = ", ".join(addr_parts)
                    prof = cust.get("companyProfile")
                    if prof:
                        clean_prof = BeautifulSoup(prof, "html.parser").get_text(" ", strip=True)
                        if clean_prof:
                            data["description"] = clean_prof
                    ed = cust.get("exhibitorDetail") or {}
                    if ed.get("boothNo"):
                        data["booth"] = ed["boothNo"]
                    if ed.get("hallNo"):
                        data["hall"] = ed["hallNo"]
                    return data
            except Exception:
                pass

    # Handle Swapcard exhibitor detail pages
    if "/exhibitor/" in source_url.lower():
        m_swap = re.search(r"/exhibitor/([A-Za-z0-9_=-]+)", source_url)
        if m_swap:
            exh_id = m_swap.group(1)
            next_el = soup.find("script", id="__NEXT_DATA__")
            exh = None
            apollo = {}
            if next_el and next_el.string:
                try:
                    ndata = json.loads(next_el.string)
                    apollo = ndata.get("props", {}).get("apolloState", {})
                    exh = apollo.get(f"Core_Exhibitor:{exh_id}")
                    if not exh:
                        for v in apollo.values():
                            if isinstance(v, dict) and v.get("_id") == exh_id:
                                exh = v
                                break
                except Exception:
                    pass

            if not exh:
                try:
                    q = f'''query {{ exhibitor: Core_exhibitor(_id: "{exh_id}") {{ _id name type description websiteUrl email address {{ street zipCode country city state }} phoneNumbers {{ number }} }} }}'''
                    resp = fetch_url(
                        "https://api.swapcard.com/graphql",
                        method="POST",
                        json={"query": q},
                        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                        timeout=10,
                    )
                    r_json = resp.json() if hasattr(resp, "json") else json.loads(resp.get("content", "{}"))
                    exh = r_json.get("data", {}).get("exhibitor")
                except Exception:
                    pass

            if exh:
                web = exh.get("websiteUrl")
                if web and _is_external_company_website(web, directory_host):
                    data["website"] = normalize_website(web)
                desc = exh.get("description")
                if desc:
                    data["description"] = normalize_string(desc)
                if exh.get("email"):
                    data["email"] = normalize_email(exh.get("email"))
                phones = [p["number"] for p in exh.get("phoneNumbers", []) if isinstance(p, dict) and p.get("number")]
                if phones:
                    data["phones"] = phones
                    data["phone"] = phones[0]
                addr = exh.get("address") or {}
                if isinstance(addr, dict):
                    data["city"] = addr.get("city")
                    data["state"] = addr.get("state")
                    data["country"] = addr.get("country")
                    parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("zipCode"), addr.get("country")]
                    non_empty = [p.strip() for p in parts if p and str(p).strip()]
                    if non_empty:
                        data["address"] = ", ".join(non_empty)
                for k, v in exh.items():
                    if k.startswith("withEvent(") and isinstance(v, dict):
                        for b in v.get("booths", []):
                            ref = b.get("__ref")
                            if ref and ref in apollo:
                                data["booth"] = apollo[ref].get("name")
                                break
                return data

    # -------------------------------------------------------------
    # 0. Identify Global Organizer Contacts from Header/Footer
    # -------------------------------------------------------------
    global_organizer_emails: set[str] = set()
    global_organizer_phones: set[str] = set()

    for el in soup.find_all(True):
        if _is_global_layout_element(el):
            for a in el.find_all("a", href=True):
                href = a["href"].strip()
                if href.lower().startswith("mailto:"):
                    for em in extract_all_emails(href):
                        global_organizer_emails.add(em.lower())
                elif href.lower().startswith("tel:"):
                    for ph in extract_all_phones(href):
                        global_organizer_phones.add(ph)

    # -------------------------------------------------------------
    # 1. Content DOM (Global Layout Elements Decomposed)
    # -------------------------------------------------------------
    import copy
    content_soup = copy.copy(soup)

    for el in content_soup.find_all(True):
        if _is_global_layout_element(el):
            el.decompose()

    KNOWN_DETAIL_LABELS = {
        "website", "web", "url", "official website", "company website", "homepage",
        "visit official website", "visit website", "view website",
        "address", "location", "headquarters", "hq", "office address", "postal address", "office location",
        "city", "town", "country", "nation", "state", "province", "region",
        "company description", "about company", "about the company", "company profile",
        "about us", "company bio", "overview", "company overview", "about", "information", "description",
        "booth details", "stall details", "booth", "stall", "stand", "booth #", "stand #", "stall no", "stall no.",
        "email", "e-mail", "contact email",
        "phone", "telephone", "tel", "mobile", "contact no", "contact number",
        "contact details", "contact info", "contact information", "contacts", "contact",
        "products on display", "company products", "products", "product list", "exhibitor products",
    }

    # -------------------------------------------------------------
    # 2. Heading / Label based DOM extraction inside Content
    # -------------------------------------------------------------
    label_elements = content_soup.find_all(
        lambda t: t.name in {"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "th", "dt", "label"}
        or (
            t.name in {"div", "span", "p"}
            and len(t.get_text(strip=True)) <= 40
            and t.get_text(strip=True).rstrip(":").strip().lower() in KNOWN_DETAIL_LABELS
        )
    )

    for el in label_elements:
        label_text = el.get_text(strip=True).rstrip(":").strip().lower()
        if not label_text:
            continue

        container = el
        if any("col" in c for c in el.get("class", [])):
            container = el
        elif el.parent and any("col" in c for c in el.parent.get("class", [])):
            container = el.parent
        elif el.name in {"b", "strong", "span"} and el.parent and el.parent.name in {"h1", "h2", "h3", "h4", "h5", "h6", "div", "p", "th", "dt"}:
            container = el.parent

        target_sib = el.find_next_sibling() or (el.parent.find_next_sibling() if el.parent and el.parent != container else None) or container.find_next_sibling()

        # --- CONTACT DETAILS (Aggregated Section) ---
        if label_text in {"contact details", "contact info", "contact information", "contacts", "contact"}:
            section_parent = container.parent if container.parent else container
            while section_parent and len(list(section_parent.children)) <= 1 and section_parent.name not in ["body", "html", "[document]"]:
                section_parent = section_parent.parent
            candidates = list(container.find_next_siblings())
            if not candidates and section_parent:
                candidates = [c for c in section_parent.children if c != container and c != container.parent and hasattr(c, "get_text")]
            for cand_el in candidates:
                cand_text = cand_el.get_text(" ", strip=True)
                cand_a = cand_el.find("a") if hasattr(cand_el, "find") else None
                # Check for Google Maps address link
                if cand_a and cand_a.has_attr("href") and not data["address"]:
                    href_l = cand_a["href"].lower()
                    if "maps.google" in href_l or "google.com/maps" in href_l or "goo.gl/maps" in href_l:
                        a_t = cand_a.get_text(strip=True)
                        if a_t and len(a_t) >= 2 and not any(k in a_t.lower() for k in ["view map", "directions"]):
                            data["address"] = normalize_string(a_t)
                # Website
                if not data["website"]:
                    if cand_a and cand_a.has_attr("href"):
                        norm_w = normalize_website(cand_a["href"])
                        if norm_w and _is_external_company_website(norm_w, directory_host):
                            data["website"] = norm_w
                    elif is_domain_or_url(cand_text):
                        norm_w = normalize_website(cand_text)
                        if norm_w and _is_external_company_website(norm_w, directory_host):
                            data["website"] = norm_w
                # Email
                for em in extract_all_emails(cand_text):
                    if em.lower() not in global_organizer_emails and em not in data["emails"]:
                        data["emails"].append(em)
                if not data["email"] and data["emails"]:
                    data["email"] = normalize_email(data["emails"])
                # Phone
                for ph in extract_all_phones(cand_text):
                    if ph not in global_organizer_phones and ph not in data["phones"]:
                        data["phones"].append(ph)
                if not data["phone"] and data["phones"]:
                    data["phone"] = data["phones"][0]
                # Address / Country (non-link item)
                if not data["address"] and hasattr(cand_el, "find") and not cand_el.find("a") and cand_text:
                    if len(cand_text) >= 2 and not any(k in cand_text.lower() for k in ["contact", "email", "phone", "website"]):
                        data["address"] = normalize_string(cand_text)

        # --- WEBSITE ---
        elif label_text in {"website", "web", "url", "official website", "company website", "homepage"}:
            if not data["website"]:
                # Check siblings of el, siblings of el.parent, siblings of container, and children of container
                cand_elements = []
                s_el = el.find_next_sibling()
                if s_el:
                    cand_elements.append(s_el)
                s_parent = el.parent.find_next_sibling() if el.parent else None
                if s_parent and s_parent not in cand_elements:
                    cand_elements.append(s_parent)
                s_cont = container.find_next_sibling()
                if s_cont and s_cont not in cand_elements:
                    cand_elements.append(s_cont)
                if container != el:
                    for ch in container.find_all(True):
                        if ch != el and ch not in el.parents and ch not in el.descendants and ch not in cand_elements:
                            cand_elements.append(ch)

                for cand_el in cand_elements:
                    a = cand_el.find("a") if hasattr(cand_el, "find") else None
                    cand = a["href"] if (a and a.has_attr("href")) else cand_el.get_text(strip=True)
                    norm = normalize_website(cand)
                    if norm and _is_external_company_website(norm, directory_host):
                        data["website"] = norm
                        break

                if not data["website"]:
                    a = container.find("a") or container.find_next("a")
                    if a and a.has_attr("href"):
                        norm = normalize_website(a["href"])
                        if norm and _is_external_company_website(norm, directory_host):
                            data["website"] = norm

        # --- ADDRESS ---
        elif label_text in {"address", "location", "headquarters", "hq", "office address", "postal address", "office location"}:
            if not data["address"]:
                sib = target_sib
                if sib:
                    txt = sib.get_text(separator=", ", strip=True)
                    if txt and len(txt) > 2:
                        data["address"] = normalize_string(txt)
                    lines = [line.strip() for line in sib.stripped_strings if line.strip()]
                    if len(lines) >= 1:
                        if not data["country"] and len(lines[-1]) < 40 and not any(ch.isdigit() for ch in lines[-1]):
                            data["country"] = lines[-1]
                        if len(lines) >= 2 and not data["city"]:
                            prev_line = lines[-2]
                            if "," in prev_line:
                                p_parts = [p.strip() for p in prev_line.split(",", 1)]
                                data["city"] = p_parts[0]
                                if not data["state"] and len(p_parts) > 1:
                                    data["state"] = re.sub(r"\b\d{4,6}\b", "", p_parts[1]).strip()
                            elif len(lines) >= 3 and not any(ch.isdigit() for ch in prev_line):
                                data["city"] = prev_line
                elif container.name in {"dt", "th"}:
                    next_val = container.find_next_sibling(["dd", "td"])
                    if next_val:
                        data["address"] = normalize_string(next_val.get_text(strip=True))

        # --- CITY ---
        elif label_text in {"city", "town"}:
            if not data["city"]:
                sib = target_sib
                if sib:
                    data["city"] = normalize_string(sib.get_text(strip=True))

        # --- COUNTRY ---
        elif label_text in {"country", "nation"}:
            if not data["country"]:
                sib = target_sib
                if sib:
                    data["country"] = normalize_string(sib.get_text(strip=True))

        # --- STATE ---
        elif label_text in {"state", "province", "region"}:
            if not data["state"]:
                sib = target_sib
                if sib:
                    data["state"] = normalize_string(sib.get_text(strip=True))

        # --- DESCRIPTION ---
        elif label_text in {
            "company description",
            "about company",
            "about the company",
            "company profile",
            "about us",
            "company bio",
            "overview",
            "company overview",
            "about",
            "information",
            "description",
        }:
            if not data["description"]:
                sib = target_sib
                if sib:
                    txt = sib.get_text(separator="\n", strip=True)
                    if txt and len(txt) > 15:
                        data["description"] = normalize_string(txt)
                elif container.parent:
                    wysiwyg = container.parent.select_one("[class*='wysiwyg'], [class*='description'], [class*='overview']")
                    if wysiwyg:
                        txt = wysiwyg.get_text(separator="\n", strip=True)
                        if txt and len(txt) > 15:
                            data["description"] = normalize_string(txt)

        # --- PRODUCTS ---
        elif label_text in {"products on display", "company products", "products", "product list", "exhibitor products"}:
            sib = target_sib
            if sib:
                lines = [line.strip() for line in sib.get_text("\n", strip=True).split("\n") if line.strip()]
                for line in lines:
                    if line not in data["products"]:
                        data["products"].append(line)

        # --- BOOTH & HALL ---
        elif label_text in {"booth details", "stall details", "booth", "stall", "stand"}:
            sib = target_sib
            if sib:
                raw_b = sib.get_text(strip=True)
                hall_m = re.search(r"hall\s*[:\-]?\s*([^\s,;|]+)", raw_b, re.IGNORECASE)
                hall_clean = (
                    re.sub(r"(?i)(?:stall|booth|stand).*", "", hall_m.group(1)).strip()
                    if hall_m else None
                )
                stall_m = re.search(
                    r"(?:stall|booth|stand)\s*[:\-]?\s*([0-9a-zA-Z\-\.]+)",
                    raw_b,
                    re.IGNORECASE,
                )
                if hall_clean and not data["hall"]:
                    data["hall"] = hall_clean
                if stall_m and not data["booth"]:
                    data["booth"] = stall_m.group(1).strip()
                if not stall_m and not data["booth"] and raw_b:
                    data["booth"] = raw_b.replace("Hall:", "").strip()

        # --- EMAIL ---
        elif label_text in {"email", "e-mail", "contact email"}:
            sib = target_sib
            if sib:
                a = sib.find("a", href=re.compile(r"^mailto:", re.I))
                em = (a.get_text(strip=True) or a["href"]) if a else sib.get_text(strip=True)
                for parsed_em in extract_all_emails(em):
                    if (
                        parsed_em.lower() not in global_organizer_emails
                        and not (directory_host and parsed_em.lower().endswith("@" + directory_host))
                        and parsed_em not in data["emails"]
                    ):
                        data["emails"].append(parsed_em)
                if not data["email"] and data["emails"]:
                    data["email"] = normalize_email(data["emails"])

        # --- PHONE ---
        elif label_text in {"phone", "telephone", "tel", "mobile", "contact no", "contact number"}:
            sib = target_sib
            if sib:
                a = sib.find("a", href=re.compile(r"^tel:", re.I))
                ph = (a.get_text(strip=True) or a["href"]) if a else sib.get_text(strip=True)
                for parsed_ph in extract_all_phones(ph):
                    if parsed_ph not in global_organizer_phones and parsed_ph not in data["phones"]:
                        data["phones"].append(parsed_ph)
                if not data["phone"] and data["phones"]:
                    data["phone"] = data["phones"][0]

    # -------------------------------------------------------------
    # 3. Dedicated Tag-Level Fallbacks inside Content
    # -------------------------------------------------------------
    # Address tag
    if not data["address"]:
        addr_tag = content_soup.find("address")
        if addr_tag:
            data["address"] = normalize_string(addr_tag.get_text(separator=", ", strip=True))

    # Mailto links - collect ONLY valid exhibitor contacts from content
    for mailto in content_soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        if any(k in mailto.get_text(strip=True).lower() for k in ["organizer", "contact organizer", "helpdesk", "support"]):
            continue
        em_val = mailto.get_text(strip=True) or mailto["href"]
        for parsed_em in extract_all_emails(em_val):
            if (
                parsed_em.lower() not in global_organizer_emails
                and not (directory_host and parsed_em.lower().endswith("@" + directory_host))
                and parsed_em not in data["emails"]
            ):
                data["emails"].append(parsed_em)
    if not data["email"] and data["emails"]:
        data["email"] = normalize_email(data["emails"])

    # Tel links - collect ONLY valid exhibitor phones from content
    for tel in content_soup.find_all("a", href=re.compile(r"^tel:", re.I)):
        if any(k in tel.get_text(strip=True).lower() for k in ["organizer", "call organizer", "helpdesk", "support"]):
            continue
        ph_val = tel.get_text(strip=True) or tel["href"]
        for parsed_ph in extract_all_phones(ph_val):
            if parsed_ph not in global_organizer_phones and parsed_ph not in data["phones"]:
                data["phones"].append(parsed_ph)
    if not data["phone"] and data["phones"]:
        data["phone"] = data["phones"][0]

    # Website link fallbacks (class/rel/target/text)
    if not data["website"]:
        for a in content_soup.find_all("a", href=True):
            href = a["href"].strip()
            a_text = a.get_text(strip=True)
            a_text_lower = a_text.lower()
            a_cls = " ".join(a.get("class", [])).lower()
            a_rel = " ".join(a.get("rel", [])).lower()

            if (
                any(k in a_text_lower for k in ["visit website", "company website", "official website", "website"])
                or any(k in a_cls for k in ["website", "site-link", "company-link", "external-link"])
                or "external" in a_rel
                or is_domain_or_url(a_text)
                or a_text_lower.startswith(("http://", "https://", "www."))
            ):
                norm = normalize_website(href)
                if norm and _is_external_company_website(norm, directory_host):
                    data["website"] = norm
                    break

    if not data["website"]:
        for a in content_soup.find_all("a", href=True):
            norm = normalize_website(a["href"].strip())
            if norm and _is_external_company_website(norm, directory_host):
                data["website"] = norm
                break

    if not data["website"]:
        content_text = content_soup.get_text(" ", strip=True)
        url_matches = re.findall(
            r"(?:https?://|www\.)[^\s,;<>]+|[a-zA-Z0-9-]+\.(?:com|in|org|net|co|io|co\.in)[^\s,;/<>]*",
            content_text,
            re.IGNORECASE,
        )
        for u in url_matches:
            norm = normalize_website(u)
            if norm and _is_external_company_website(norm, directory_host):
                data["website"] = norm
                break

    # Products extraction inside content
    product_cards = content_soup.find_all(
        lambda t: t.name in {"div", "li", "article"} and any(
            cls_part in " ".join(t.get("class", [])).lower()
            for cls_part in ["productcard", "product-item", "product_item", "exhibit-item"]
        )
    )
    for pc in product_cards:
        p_name = None
        name_label = pc.find(lambda t: t.name in {"h6", "b", "strong"} and "name" in t.get_text(strip=True).lower())
        if name_label:
            sib = name_label.find_next_sibling()
            if sib:
                p_name = sib.get_text(strip=True)
        if not p_name:
            h = pc.find(["h3", "h4", "h5", "h6"])
            if h:
                p_name = h.get_text(strip=True)
        if p_name and p_name not in data["products"]:
            norm_p = normalize_string(p_name)
            if norm_p and len(norm_p) > 1 and norm_p not in data["products"]:
                data["products"].append(norm_p)

    # -------------------------------------------------------------
    # 3. JSON-LD / Structured Data (Fallback only if missing)
    # -------------------------------------------------------------
    if not data["website"] or not data["address"] or not data["description"]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("@type", "")).lower()
                    item_name = str(item.get("name", "")).lower()
                    # Skip organizer / expo schema
                    if any(x in item_name for x in ["expo", "exhibition", directory_host]):
                        continue

                    if item_type in {"organization", "corporation", "localbusiness"}:
                        if not data["website"] and item.get("url"):
                            cand = normalize_website(item["url"])
                            if cand and _is_external_company_website(cand, directory_host):
                                data["website"] = cand
                        if not data["address"] and item.get("address"):
                            addr_val = item["address"]
                            if isinstance(addr_val, dict):
                                parts = [
                                    addr_val.get("streetAddress"),
                                    addr_val.get("addressLocality"),
                                    addr_val.get("addressRegion"),
                                    addr_val.get("postalCode"),
                                    addr_val.get("addressCountry"),
                                ]
                                data["address"] = ", ".join(str(p) for p in parts if p)
                            elif isinstance(addr_val, str):
                                data["address"] = addr_val
                        if not data["description"] and item.get("description"):
                            data["description"] = item["description"]
                        if not data["email"] and item.get("email"):
                            data["email"] = normalize_email(item["email"])
                        if not data["phone"] and item.get("telephone"):
                            data["phone"] = normalize_phone(item["telephone"])
            except Exception:
                pass

    # -------------------------------------------------------------
    # 4. Embedded Script JSON State (Next.js, Apollo, React state)
    # -------------------------------------------------------------
    if not data["website"] or not data["description"]:
        for script in soup.find_all("script"):
            if script.string and any(k in script.string for k in ["websiteUrl", "website", "__NEXT_DATA__"]):
                try:
                    js_data = json.loads(script.string)

                    def scan_json(obj):
                        if isinstance(obj, dict):
                            if not data["website"]:
                                w = obj.get("websiteUrl") or obj.get("website")
                                if isinstance(w, str) and _is_external_company_website(w, directory_host):
                                    norm_w = normalize_website(w)
                                    if norm_w:
                                        data["website"] = norm_w
                            if not data["description"]:
                                d = obj.get("description")
                                if isinstance(d, str) and len(d) > 20 and not d.startswith("http"):
                                    data["description"] = normalize_string(d)
                            for v in obj.values():
                                scan_json(v)
                        elif isinstance(obj, list):
                            for it in obj:
                                scan_json(it)

                    scan_json(js_data)
                except Exception:
                    pass

    return data


def discover_detail_urls_from_html(html_content: str, base_url: str) -> dict[str, str]:
    """
    Deterministically extract mapping of lowercase company name -> detail URL from list HTML.
    Supports:
    - JS variables (e.g. var exhibitorDetailsBaseUrl = '/ExhibitorCompany')
    - onclick handlers (e.g. OpenDetail('Company Name'))
    - Standard anchor links with company names and detail href patterns
    """
    if not html_content:
        return {}

    name_to_url: dict[str, str] = {}
    base_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"

    # 1. Check for JS base URL pattern
    js_base_match = re.search(
        r"var\s+exhibitorDetailsBaseUrl\s*=\s*['\"]([^'\"]+)['\"]",
        html_content,
        re.IGNORECASE,
    )
    exhibitor_base = js_base_match.group(1).strip() if js_base_match else None

    # 2. Check for onclick patterns like OpenDetail('...') or openCompany('...')
    onclick_matches = re.findall(
        r"""(?:OpenDetail|openCompany|goToDetail|showExhibitor)\s*\(\s*['"]([^'"]+)['"]\s*\)""",
        html_content,
        re.IGNORECASE,
    )
    for comp in onclick_matches:
        comp_clean = normalize_company_name(comp)
        if comp_clean and exhibitor_base:
            separator = "?" if "?" not in exhibitor_base else "&"
            full_url = urljoin(base_origin, f"{exhibitor_base}{separator}Company={quote(comp_clean)}")
            name_to_url[comp_clean.lower()] = full_url

    # 3. Parse standard HTML <a> links
    soup = BeautifulSoup(html_content, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Look for detail URL signatures in path
        href_lower = href.lower()
        is_detail_link = any(
            sig in href_lower
            for sig in [
                "/co/", "/co?", "/exhibitor/", "/exhibitors/", "/company/", "/companies/",
                "/profile/", "/stand/", "/booth/", "/detail/", "/exhibitorcompany",
                "/exhibitor-detail/", "/exhibitor-details/",
            ]
        )

        link_text = normalize_company_name(a.get_text(strip=True))
        title_text = normalize_company_name(a.get("title", ""))
        comp_key = link_text or title_text

        if is_detail_link and comp_key and len(comp_key) > 2:
            resolved = urljoin(base_url, href)
            name_to_url[comp_key.lower()] = resolved

        # Also check child heading / strong / span if present
        if is_detail_link:
            for child in a.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "span", "p"]):
                cand = normalize_company_name(child.get_text(strip=True))
                if cand and 2 <= len(cand) <= 80:
                    name_to_url[cand.lower()] = urljoin(base_url, href)

    return name_to_url


def associate_detail_urls(
    records: list[ExhibitorRecord],
    html_content: str,
    base_url: str,
) -> None:
    """
    Associate detail_url with records that currently lack one.
    """
    if not records:
        return

    mapping = discover_detail_urls_from_html(html_content, base_url)

    # Also detect JS base URL for general generation
    js_base_match = re.search(
        r"var\s+exhibitorDetailsBaseUrl\s*=\s*['\"]([^'\"]+)['\"]",
        html_content,
        re.IGNORECASE,
    )
    exhibitor_base = js_base_match.group(1).strip() if js_base_match else None
    base_origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"

    # Also detect JS link templates, e.g. href='ExhibitorDetails2025.aspx?ExhiId=' + e.ExhibitorId
    # or href='/showcompanydetails.php?id=' + company.id
    js_template_match = re.search(
        r"href\s*=\s*['\"]([^'\"<>\s]+\?(?:exhiid|exhibitorid|exhibitor_id|exhid|id)=)['\"]\s*\+\s*(?:[a-zA-Z0-9_]+\.)?([a-zA-Z0-9_]+)",
        html_content,
        re.IGNORECASE,
    )
    js_detail_base = js_template_match.group(1).strip() if js_template_match else None

    for rec in records:
        if rec.detail_url:
            continue

        c_name = normalize_company_name(rec.company_name).lower()
        if c_name in mapping:
            rec.detail_url = mapping[c_name]
        elif exhibitor_base and rec.company_name:
            separator = "?" if "?" not in exhibitor_base else "&"
            rec.detail_url = urljoin(
                base_origin,
                f"{exhibitor_base}{separator}Company={quote(rec.company_name)}",
            )
        elif js_detail_base and rec.exhibitor_id:
            rec.detail_url = urljoin(base_url, f"{js_detail_base}{rec.exhibitor_id}")


def enrich_records(
    records: list[ExhibitorRecord],
    budget: ScrapeBudget,
    current_requests: int,
    max_enrich: Optional[int] = None,
) -> tuple[list[ExhibitorRecord], int]:
    """
    Deterministically enrich records that have a detail_url with website, address,
    description, and other details. Strictly respects budget.max_requests.
    """
    if not records:
        return records, 0

    remaining_requests = max(0, budget.max_requests - current_requests)
    if remaining_requests <= 0:
        return records, 0

    limit = remaining_requests
    if max_enrich is not None:
        limit = min(limit, max_enrich)

    requests_made = 0
    cache: dict[str, dict] = {}

    # Special batch optimization for mmiconnect.in GraphQL
    mmi_records = [
        (i, r) for i, r in enumerate(records)
        if (not r.website) and (
            (r.detail_url and "mmiconnect" in r.detail_url.lower())
            or (r.source_url and "mmiconnect" in r.source_url.lower())
        )
    ]
    if mmi_records and requests_made < limit:
        sample_url = mmi_records[0][1].source_url or mmi_records[0][1].detail_url
        m = re.search(r"/catalogue/(?:exhibitors|exhibitor-detail|home|products)/([^/?#]+)", sample_url, re.IGNORECASE)
        group_slug = m.group(1) if m else "ifat2026"

        try:
            mmi_endpoint = "https://mmiconnect.in/graphql"
            batch_query = """query getExhibitorListForGroup($first: Int, $after: Int, $group: String) {
  catalogueQueries {
    exhibitorsWithWishListGroup(
      first: $first
      after: $after
      group: $group
    ) {
      exhibitors {
        customer {
          id
          companyName
          country
          city
          website
          userId
          showId
          exhibitorDetail {
            boothNo
            hallNo
          }
        }
      }
    }
  }
}"""
            resp = fetch_url(
                mmi_endpoint,
                method="POST",
                json={
                    "operationName": "getExhibitorListForGroup",
                    "variables": {"first": 1000, "after": -1, "group": group_slug},
                    "query": batch_query,
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                    "Authorization": "Bearer null",
                },
                timeout=15,
            )
            batch_data = resp.json()
            requests_made += 1
            ex_list = (
                batch_data.get("data", {})
                .get("catalogueQueries", {})
                .get("exhibitorsWithWishListGroup", {})
                .get("exhibitors", [])
            )
            mmi_site_map = {}
            for item in ex_list:
                c = item.get("customer") or {}
                c_name = normalize_company_name(c.get("companyName", "")).lower()
                web = c.get("website")
                city = c.get("city")
                if c_name:
                    mmi_site_map[c_name] = {"website": web, "city": city}

            for idx, r in mmi_records:
                c_clean = normalize_company_name(r.company_name).lower()
                if c_clean in mmi_site_map:
                    info = mmi_site_map[c_clean]
                    if info.get("website") and not r.website:
                        r.website = normalize_website(info["website"])
                    if info.get("city") and not r.city:
                        r.city = normalize_string(info["city"])
        except Exception:
            pass

    for i, rec in enumerate(records):
        if requests_made >= limit:
            break

        # Only enrich if detail_url is present and key fields are missing
        if not rec.detail_url:
            continue
        if rec.website and rec.address and rec.description:
            continue

        detail_url = rec.detail_url
        if detail_url in cache:
            detail_data = cache[detail_url]
        else:
            try:
                resp = fetch_url(detail_url, timeout=budget.timeout_seconds)
                requests_made += 1
                content = (
                    resp.content
                    if hasattr(resp, "content")
                    else resp.get("content", "")
                )
                final_url = (
                    resp.final_url
                    if hasattr(resp, "final_url")
                    else resp.get("url", detail_url)
                )
                detail_data = extract_detail_fields(content, final_url)
                cache[detail_url] = detail_data
            except Exception:
                detail_data = {}
                cache[detail_url] = {}

        if not detail_data:
            continue

        # Merge fields conservatively
        new_website = rec.website or detail_data.get("website")
        new_address = rec.address or detail_data.get("address")
        new_description = rec.description or detail_data.get("description")
        new_email = rec.email or detail_data.get("email")
        new_phone = rec.phone or detail_data.get("phone")
        new_booth = rec.booth or detail_data.get("booth")
        new_hall = rec.hall or detail_data.get("hall")

        merged_emails = list(rec.emails)
        for em in detail_data.get("emails", []):
            if em not in merged_emails:
                merged_emails.append(em)
        if detail_data.get("email") and detail_data["email"] not in merged_emails:
            merged_emails.append(detail_data["email"])

        merged_phones = list(rec.phones)
        for ph in detail_data.get("phones", []):
            if ph not in merged_phones:
                merged_phones.append(ph)
        if detail_data.get("phone") and detail_data["phone"] not in merged_phones:
            merged_phones.append(detail_data["phone"])

        merged_products = list(rec.products)
        for p in detail_data.get("products", []):
            if p not in merged_products:
                merged_products.append(p)

        merged_categories = list(rec.category)
        for c in detail_data.get("category", []):
            if c not in merged_categories:
                merged_categories.append(c)

        records[i] = normalize_record(
            ExhibitorRecord(
                exhibitor_id=rec.exhibitor_id,
                company_name=rec.company_name,
                description=new_description,
                website=new_website,
                email=new_email,
                emails=merged_emails,
                phone=new_phone,
                phones=merged_phones,
                address=new_address,
                country=rec.country or detail_data.get("country"),
                city=rec.city or detail_data.get("city"),
                state=rec.state or detail_data.get("state"),
                booth=new_booth,
                hall=new_hall,
                category=merged_categories,
                products=merged_products,
                detail_url=rec.detail_url,
                source_url=rec.source_url,
                confidence=min(1.0, rec.confidence + (0.05 if new_website else 0.0)),
            )
        )

    return records, requests_made


def clean_global_organizer_contacts(
    records: list[ExhibitorRecord],
    source_html: Optional[str] = None,
    source_url: Optional[str] = None,
) -> list[ExhibitorRecord]:
    """
    Detect and strip site-wide repeated organizer contact information (emails, phones)
    that belong to the exhibition website footer/header rather than individual exhibitors.
    Preserves genuine shared contacts (e.g. sister companies with matching website/domain).
    """
    if not records:
        return records

    directory_host = urlparse(source_url).netloc.lower() if source_url else ""
    footer_emails: set[str] = set()
    footer_phones: set[str] = set()

    # 1. Extract confirmed global footer/header emails & phones from source HTML
    if source_html:
        soup = BeautifulSoup(source_html, "html.parser")
        for el in soup.find_all(True):
            if _is_global_layout_element(el):
                for a in el.find_all("a", href=True):
                    h = a["href"].strip()
                    if h.lower().startswith("mailto:"):
                        for em in extract_all_emails(h):
                            footer_emails.add(em.lower())
                    elif h.lower().startswith("tel:"):
                        for ph in extract_all_phones(h):
                            footer_phones.add(ph)

    # 2. Count frequency across all records
    from collections import Counter
    email_counts = Counter()
    phone_counts = Counter()
    for rec in records:
        for em in rec.emails:
            email_counts[em.lower()] += 1
        if rec.email and rec.email.lower() not in [e.lower() for e in rec.emails]:
            email_counts[rec.email.lower()] += 1

        for ph in rec.phones:
            phone_counts[ph] += 1
        if rec.phone and rec.phone not in rec.phones:
            phone_counts[rec.phone] += 1

    # 3. Determine which emails/phones are global organizer contacts
    disallowed_emails: set[str] = set(footer_emails)
    disallowed_phones: set[str] = set(footer_phones)

    total_recs = len(records)
    for em, count in email_counts.items():
        if em in footer_emails:
            disallowed_emails.add(em)
            continue

        if count >= 3 or (total_recs >= 3 and count / total_recs >= 0.25):
            matching_recs = [
                r for r in records
                if (r.email and r.email.lower() == em) or any(e.lower() == em for e in r.emails)
            ]
            em_domain = em.split("@")[-1] if "@" in em else ""
            domain_root = em_domain.split(".")[0] if "." in em_domain else em_domain
            is_organizer_domain = (
                bool(directory_host and (em_domain == directory_host or em_domain.endswith("." + directory_host) or directory_host.endswith("." + em_domain)))
                or any(k in em for k in ["organizer", "expo@", "exhibition@", "fair@", "helpdesk@", "support@"])
            )
            # Check if matching records share this brand in their company name or website
            company_words = {w for r in matching_recs for w in r.company_name.lower().split() if len(w) > 2}
            company_hosts = {urlparse(r.website).netloc.lower() for r in matching_recs if r.website}
            shares_company_brand = (
                (domain_root in company_words or any(w in domain_root for w in company_words))
                or (len(company_hosts) == 1 and all(domain_root in h for h in company_hosts))
            )
            if is_organizer_domain or not shares_company_brand:
                disallowed_emails.add(em)

    for ph, count in phone_counts.items():
        if ph in footer_phones:
            disallowed_phones.add(ph)
            continue

        if count >= 3 or (total_recs >= 3 and count / total_recs >= 0.25):
            matching_recs = [r for r in records if r.phone == ph or ph in r.phones]
            company_names = {r.company_name.lower().split()[0] for r in matching_recs if r.company_name}
            websites = {urlparse(r.website).netloc.lower() for r in matching_recs if r.website}
            shares_company_brand = len(company_names) == 1 or (len(websites) == 1 and bool(websites))
            if not shares_company_brand:
                disallowed_phones.add(ph)

    # 4. Strip disallowed emails and phones from records
    cleaned_records = []
    for rec in records:
        changed = False
        new_email = rec.email
        new_emails = [e for e in rec.emails if e.lower() not in disallowed_emails]
        if rec.email and rec.email.lower() in disallowed_emails:
            new_email = new_emails[0] if new_emails else None
            changed = True
        elif not new_email and new_emails:
            new_email = new_emails[0]
            changed = True

        new_phone = rec.phone
        new_phones = [p for p in rec.phones if p not in disallowed_phones]
        if rec.phone and rec.phone in disallowed_phones:
            new_phone = new_phones[0] if new_phones else None
            changed = True
        elif not new_phone and new_phones:
            new_phone = new_phones[0]
            changed = True

        if changed or len(new_emails) != len(rec.emails) or len(new_phones) != len(rec.phones):
            rec_dict = rec.model_dump()
            rec_dict["email"] = new_email
            rec_dict["emails"] = new_emails
            rec_dict["phone"] = new_phone
            rec_dict["phones"] = new_phones
            cleaned_records.append(ExhibitorRecord(**rec_dict))
        else:
            cleaned_records.append(rec)

    return cleaned_records
