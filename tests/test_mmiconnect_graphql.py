import pytest
from app.extraction.html_records import _is_valid_company_name
from app.extraction.normalizer import normalize_api_record
from app.extraction.api_records import extract_api_records, find_record_collection
from app.discovery.api import evaluate_api_payload
from app.discovery.repeated_elements import find_repeated_elements


def test_ui_color_palette_blacklisted():
    for color in [
        "AMBER", "BLUE", "CYAN", "DEEP-ORANGE", "DEEP-PURPLE",
        "GRAY", "GREEN", "ORANGE", "PINK", "PURPLE", "RED", "TEAL"
    ]:
        assert not _is_valid_company_name(color)
        assert not _is_valid_company_name(color.lower())


def test_find_repeated_elements_ignores_scripts_and_styles():
    html = """
    <html>
      <head>
        <script src="a.js"></script>
        <script src="b.js"></script>
        <script src="c.js"></script>
        <style>.a{}</style>
        <style>.b{}</style>
        <style>.c{}</style>
        <link rel="stylesheet" href="a.css">
        <link rel="stylesheet" href="b.css">
        <link rel="stylesheet" href="c.css">
      </head>
      <body>
        <div>Hello</div>
      </body>
    </html>
    """
    repeated = find_repeated_elements(html)
    sigs = [r["signature"] for r in repeated]
    assert "script" not in sigs
    assert "style" not in sigs
    assert "link" not in sigs


def test_mmi_graphql_customer_unwrapping():
    sample_graphql_item = {
        "customer": {
            "id": 1054046,
            "companyName": "AERZEN MACHINES INDIA PVT. LTD.",
            "country": "India",
            "city": "Vadodara",
            "website": "https://www.aerzen.com",
            "userId": "2fd23130-3cb8-4350-8a27-bfc978a31aff",
            "showId": 115,
            "exhibitorDetail": {
                "boothNo": "J021",
                "hallNo": "6",
                "__typename": "ExhibitorDetail"
            },
            "__typename": "Customer"
        },
        "customerRating": None,
        "appoinment": None,
        "__typename": "InterestedCustomer"
    }

    record = normalize_api_record(
        sample_graphql_item,
        source_url="https://mmiconnect.in/app/catalogue/exhibitors/ifat2026?first=1000"
    )

    assert record.company_name == "AERZEN MACHINES INDIA PVT. LTD."
    assert record.country == "India"
    assert record.city == "Vadodara"
    assert record.booth == "J021"
    assert record.hall == "6"
    assert record.website == "https://www.aerzen.com"
    assert record.detail_url == "https://mmiconnect.in/app/catalogue/exhibitor-detail/ifat2026/115/2fd23130-3cb8-4350-8a27-bfc978a31aff"


def test_mmi_graphql_evaluation():
    graphql_payload = {
        "data": {
            "catalogueQueries": {
                "exhibitorsWithWishListGroup": {
                    "totalCount": 2,
                    "exhibitors": [
                        {
                            "customer": {
                                "id": 1054046,
                                "companyName": "AERZEN MACHINES INDIA PVT. LTD.",
                                "country": "India",
                                "userId": "2fd23130-3cb8-4350-8a27-bfc978a31aff",
                                "showId": 115,
                                "exhibitorDetail": {
                                    "boothNo": "J021",
                                    "hallNo": "6"
                                }
                            }
                        },
                        {
                            "customer": {
                                "id": 1220729,
                                "companyName": "AL HASAN MARINE",
                                "country": "India",
                                "userId": "0405d345-3711-41bf-8bbc-93fd58591246",
                                "showId": 115,
                                "exhibitorDetail": {
                                    "boothNo": "D067",
                                    "hallNo": "4"
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    eval_result = evaluate_api_payload(
        graphql_payload,
        url="https://mmiconnect.in/graphql"
    )

    assert eval_result["valid_exhibitor_api"] is True
    assert eval_result["useful_records"] == 2
    assert eval_result["records"][0].company_name == "AERZEN MACHINES INDIA PVT. LTD."
    assert eval_result["records"][0].booth == "J021"
    assert eval_result["records"][0].hall == "6"
