from bs4 import BeautifulSoup
from urllib.parse import urljoin

from app.models.exhibitor import ExhibitorRecord
from app.extraction.normalizer import normalize_record


def extract_repeated_records(
    html: str,
    source_url: str,
    signature: str,
) -> list[ExhibitorRecord]:

    soup = BeautifulSoup(html, "html.parser")

    # Example signature:
    # div.card
    parts = signature.split(".", 1)

    tag_name = parts[0]
    class_name = parts[1] if len(parts) > 1 else None

    if class_name:
        elements = soup.find_all(
            tag_name,
            class_=class_name
        )
    else:
        elements = soup.find_all(tag_name)

    records = []

    for element in elements:

        # --------------------------------
        # Find possible company/name
        # --------------------------------

        name = None

        for heading in element.find_all(
            ["h1", "h2", "h3", "h4", "h5", "h6"]
        ):
            text = heading.get_text(
                " ",
                strip=True
            )

            if text:
                name = text
                break

        # Fallback: use first meaningful text
        if not name:
            text = element.get_text(
                " ",
                strip=True
            )

            if text:
                name = text[:200]

        if not name:
            continue

        # --------------------------------
        # Find possible detail link
        # --------------------------------

        detail_url = None

        link = element.find(
            "a",
            href=True
        )

        if link:
            detail_url = urljoin(
                source_url,
                link["href"]
            )

        # --------------------------------
        # Create record
        # --------------------------------

        records.append(
            normalize_record(
                ExhibitorRecord(
                    company_name=name,
                    detail_url=detail_url,
                    source_url=source_url,
                    confidence=0.7,
                )
            )
        )

    return records