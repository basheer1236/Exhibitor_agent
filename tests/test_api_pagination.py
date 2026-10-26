from unittest.mock import patch

from app.extraction.api_pagination import (
    build_api_page_url,
    fetch_api_pages,
)
from app.models.scrape_budget import ScrapeBudget


def test_build_api_page_url():

    url = (
        "https://example.com/api/exhibitors"
        "?page=1"
    )

    result = build_api_page_url(
        url,
        "page",
        3,
    )

    assert result == (
        "https://example.com/api/exhibitors"
        "?page=3"
    )


def test_build_api_page_url_without_parameter():

    url = (
        "https://example.com/api/exhibitors"
    )

    result = build_api_page_url(
        url,
        "page",
        2,
    )

    assert result == (
        "https://example.com/api/exhibitors"
        "?page=2"
    )


@patch("app.extraction.api_pagination.requests.get")
def test_fetch_api_pages(mock_get):

    responses = [
        {
            "data": [
                {
                    "name": "ABC Technologies",
                },
            ]
        },
        {
            "data": [
                {
                    "name": "XYZ Solutions",
                },
            ]
        },
        {
            "data": []
        },
    ]

    class MockResponse:

        def __init__(self, payload):
            self.payload = payload
            self.ok = True
            self.status_code = 200

        def json(self):
            return self.payload

    mock_get.side_effect = [
        MockResponse(responses[0]),
        MockResponse(responses[1]),
        MockResponse(responses[2]),
    ]

    budget = ScrapeBudget(
        max_pages=10,
        max_requests=10,
        max_records=100,
        timeout_seconds=5,
    )

    pages_iter = fetch_api_pages(
        "https://example.com/api/exhibitors",
        "page",
        budget,
    )

    from app.extraction.api_records import extract_api_records
    records = []
    for page in pages_iter:
        page_records = extract_api_records(page["payload"], page["url"])
        if not page_records:
            break
        records.extend(page_records)

    assert len(records) == 2

    assert records[0].company_name == (
        "ABC Technologies"
    )

    assert records[1].company_name == (
        "XYZ Solutions"
    )

    assert mock_get.call_count == 3