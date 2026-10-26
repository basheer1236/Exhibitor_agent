from app.extraction.api_records import (
    extract_api_records,
    find_record_collection,
)


def test_direct_list_response():

    payload = [
        {
            "name": "ABC Technologies",
            "country": "India",
        },
        {
            "name": "XYZ Solutions",
            "country": "Germany",
        },
    ]

    records = extract_api_records(
        payload,
        "https://example.com/api/exhibitors",
    )

    assert len(records) == 2

    assert records[0].company_name == (
        "ABC Technologies"
    )

    assert records[0].country == "India"


def test_data_response():

    payload = {
        "data": [
            {
                "name": "ABC Technologies",
            },
            {
                "name": "XYZ Solutions",
            },
        ]
    }

    records = extract_api_records(
        payload,
        "https://example.com/api/exhibitors",
    )

    assert len(records) == 2


def test_results_response():

    payload = {
        "results": [
            {
                "companyName": "ABC Technologies",
            }
        ]
    }

    records = extract_api_records(
        payload,
        "https://example.com/api/exhibitors",
    )

    assert len(records) == 1

    assert records[0].company_name == (
        "ABC Technologies"
    )


def test_unknown_response():

    payload = {
        "message": "No exhibitors found"
    }

    records = extract_api_records(
        payload,
        "https://example.com/api/exhibitors",
    )

    assert records == []


def test_hits_response_with_nested_exhibitor_and_halls():
    """Verify that search hits containing nested exhibitor objects and jumpLabels extract cleanly."""
    payload = {
        "result": {
            "jumpLabels": [
                {"hasResults": False, "label": "#", "id": "0-9"},
                {"hasResults": True, "label": "A", "id": "a"},
            ],
            "hits": [
                {
                    "score": 1.0,
                    "exhibitor": {
                        "id": "mf_123",
                        "rewriteId": "3tech-textil-gmbh",
                        "name": "3tech Textil GmbH",
                        "shortDescription": "High-tech woven fabrics manufacturer",
                        "exhibition": {
                            "exhibitionHall": [
                                {
                                    "hall": "12.1",
                                    "stand": [{"name": "C62"}]
                                }
                            ]
                        },
                        "address": {
                            "city": "Radevormwald",
                            "country": {"label": "Germany"},
                            "email": "info@3tech.de",
                            "tel": "+49 202 12345"
                        },
                        "homepage": "www.3tech-textil.de"
                    }
                }
            ]
        }
    }

    records = extract_api_records(
        payload,
        "https://techtextil.messefrankfurt.com/frankfurt/en/exhibitor-search.html?page=1&pagesize=30",
    )

    assert len(records) == 1
    r = records[0]
    assert r.company_name == "3tech Textil GmbH"
    assert r.hall == "12.1"
    assert r.booth == "C62"
    assert r.city == "Radevormwald"
    assert r.country == "Germany"
    assert r.email == "info@3tech.de"
    assert r.website == "https://www.3tech-textil.de"
    assert "3tech-textil-gmbh.html" in r.detail_url