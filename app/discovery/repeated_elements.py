from collections import Counter
from bs4 import BeautifulSoup


def get_element_signature(element) -> str:
    """
    Creates a simple structural signature for an HTML element.
    """

    tag = element.name

    classes = element.get("class", [])

    class_part = ".".join(sorted(classes))

    if class_part:
        return f"{tag}.{class_part}"

    return tag


IGNORED_ELEMENT_TAGS = {
    "script", "style", "link", "meta", "noscript", "title", "head", "html", "body", "br", "hr",
}


def find_repeated_elements(
    html: str,
    minimum_repetitions: int = 3
) -> list[dict]:

    soup = BeautifulSoup(html, "html.parser")

    signatures = Counter()

    for element in soup.find_all(True):
        if not element.name or element.name in IGNORED_ELEMENT_TAGS:
            continue

        signature = get_element_signature(element)

        signatures[signature] += 1

    repeated = []

    for signature, count in signatures.items():

        if count >= minimum_repetitions:

            repeated.append({
                "signature": signature,
                "count": count
            })

    repeated.sort(
        key=lambda item: item["count"],
        reverse=True
    )

    return repeated