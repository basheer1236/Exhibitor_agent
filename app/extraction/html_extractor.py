from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    results = []

    for link in soup.find_all("a", href=True):

        text = link.get_text(" ", strip=True)

        if not text:
            continue

        results.append({
            "text": text,
            "url": urljoin(base_url, link["href"])
        })

    return results

def extract_candidate_records(
    html: str,
    base_url: str
) -> list[dict]:

    soup = BeautifulSoup(html, "html.parser")

    records = []

    for link in soup.find_all("a", href=True):

        text = link.get_text(" ", strip=True)

        if not text:
            continue

        href = urljoin(base_url, link["href"])

        if not is_likely_detail_link(text, href):
            continue

        records.append({
            "name": text,
            "detail_url": href,
            "source_url": base_url
        })

    return records

def is_likely_detail_link(
    text: str,
    url: str
) -> bool:

    text_lower = text.lower()
    url_lower = url.lower()

    keywords = [
        "exhibitor",
        "company",
        "supplier",
        "vendor",
        "manufacturer",
        "profile",
        "member",
        "brand",
        "partner"
    ]

    combined = f"{text_lower} {url_lower}"

    return any(
        keyword in combined
        for keyword in keywords
    )

def deduplicate_candidates(
    records: list[dict]
) -> list[dict]:

    seen = set()
    unique = []

    for record in records:

        url = record["detail_url"]

        if url in seen:
            continue

        seen.add(url)
        unique.append(record)

    return unique