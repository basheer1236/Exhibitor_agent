import requests
from app.discovery.http import fetch_url, _ORIGINAL_REQUESTS_GET


def fetch_api(
    url: str,
    timeout: int = 30
):
    if requests.get is not _ORIGINAL_REQUESTS_GET:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent":
                    "Mozilla/5.0 (compatible; UniversalExhibitorAgent/1.0)"
            }
        )
        response.raise_for_status()
        return {
            "url": response.url,
            "status_code": response.status_code,
            "data": response.json()
        }

    response = fetch_url(
        url,
        timeout=timeout,
        headers={
            "User-Agent":
                "Mozilla/5.0 (compatible; UniversalExhibitorAgent/1.0)"
        }
    )

    response.raise_for_status()

    return {
        "url": response.url,
        "status_code": response.status_code,
        "data": response.json()
    }