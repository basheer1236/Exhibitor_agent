from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    results = []

    for link in soup.find_all("a", href=True):

        text = link.get_text(" ", strip=True)
        href = urljoin(base_url, link["href"])

        if not text:
            continue

        results.append({
            "text": text,
            "url": href
        })

    return results

def find_detail_links(
    html: str,
    base_url: str
) -> list[str]:

    soup = BeautifulSoup(html, "html.parser")

    detail_links = []

    keywords = [
        "exhibitor",
        "company",
        "profile",
        "supplier",
        "vendor",
        "member"
    ]

    for link in soup.find_all("a", href=True):

        href = urljoin(base_url, link["href"])
        text = link.get_text(" ", strip=True).lower()

        combined = f"{text} {href.lower()}"

        if any(keyword in combined for keyword in keywords):

            detail_links.append(href)

    return list(set(detail_links))