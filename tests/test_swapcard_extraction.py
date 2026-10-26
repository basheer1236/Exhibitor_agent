import json
import pytest
from unittest.mock import patch, MagicMock

from app.models.exhibitor import ExhibitorRecord
from app.models.scrape_budget import ScrapeBudget
from app.models.scrape_result import ScrapeResult
from app.agents.universal_scraper import (
    _extract_swapcard_single_exhibitor,
    _scrape_swapcard_directory,
    scrape,
)
from app.extraction.detail_enricher import extract_detail_fields
from app.extraction.normalizer import normalize_record


MOCK_SWAPCARD_DETAIL_HTML = """
<html>
  <head>
    <script id="__NEXT_DATA__" type="application/json">
    {
      "props": {
        "apolloState": {
          "Core_Exhibitor:RXhoaWJpdG9yXzEyMzQ1": {
            "__typename": "Core_Exhibitor",
            "_id": "RXhoaWJpdG9yXzEyMzQ1",
            "name": "BioPharm Global Ltd",
            "websiteUrl": "https://biopharm-global.com",
            "description": "Leading global biopharmaceutical manufacturer.",
            "email": "info@biopharm-global.com",
            "address": {
              "street": "100 Innovation Way",
              "city": "Cambridge",
              "state": "MA",
              "zipCode": "02142",
              "country": "United States"
            },
            "phoneNumbers": [
              { "number": "+1 617-555-0199" }
            ],
            "withEvent({\\"eventId\\":\\"RXZlbnRfMTEx\\"})": {
              "booths": [
                { "__ref": "Core_Location:TWVldGluZ1BsYWNlXzU1NQ==" }
              ]
            }
          },
          "Core_Location:TWVldGluZ1BsYWNlXzU1NQ==": {
            "id": "TWVldGluZ1BsYWNlXzU1NQ==",
            "name": "Hall 5 / Booth 502"
          }
        }
      }
    }
    </script>
  </head>
  <body></body>
</html>
"""


def test_swapcard_detail_from_html():
    url = "https://visitor.cphieventplanner.com/event/cphi-milan-2026/exhibitor/RXhoaWJpdG9yXzEyMzQ1"
    rec = _extract_swapcard_single_exhibitor(MOCK_SWAPCARD_DETAIL_HTML, url)
    assert rec is not None
    assert rec.company_name == "BioPharm Global Ltd"
    assert rec.website == "https://biopharm-global.com"
    assert rec.booth == "Hall 5 / Booth 502"
    assert rec.city == "Cambridge"
    assert rec.state == "MA"
    assert rec.country == "United States"
    assert "100 Innovation Way" in rec.address
    assert rec.email == "info@biopharm-global.com"
    assert rec.phone == "+1 617-555-0199"


def test_swapcard_detail_enricher_integration():
    url = "https://visitor.cphieventplanner.com/event/cphi-milan-2026/exhibitor/RXhoaWJpdG9yXzEyMzQ1"
    fields = extract_detail_fields(MOCK_SWAPCARD_DETAIL_HTML, url)
    assert fields["website"] == "https://biopharm-global.com"
    assert fields["booth"] == "Hall 5 / Booth 502"
    assert fields["city"] == "Cambridge"
    assert fields["country"] == "United States"


def test_swapcard_directory_pagination():
    url = "https://visitor.cphieventplanner.com/event/cphi-milan-2026/exhibitors/RXZlbnRWaWV3XzEyNzIwOTk="
    budget = ScrapeBudget(max_records=4, max_pages=2)
    from datetime import datetime, timezone
    result = ScrapeResult(source_url=url, final_url=url, started_at=datetime.now(timezone.utc))

    mock_profile = type("Profile", (), {
        "url": url,
        "initial_content": """<script id="__NEXT_DATA__">{"props":{"event":{"id":"RXZlbnRfOTk5"}}}</script>"""
    })()

    page1_data = {
        "data": {
            "view": {
                "exhibitors": {
                    "totalCount": 4,
                    "pageInfo": {
                        "hasNextPage": True,
                        "endCursor": "cursor_page_1"
                    },
                    "nodes": [
                        {
                            "_id": "Ex1",
                            "name": "Company One",
                            "websiteUrl": "https://company-one.com",
                            "address": {"country": "Germany", "city": "Munich"},
                            "withEvent": {"booths": [{"name": "1A10"}]}
                        },
                        {
                            "_id": "Ex2",
                            "name": "Company Two",
                            "websiteUrl": "https://company-two.com",
                            "address": {"country": "France", "city": "Paris"},
                            "withEvent": {"booths": [{"name": "2B20"}]}
                        }
                    ]
                }
            }
        }
    }

    page2_data = {
        "data": {
            "view": {
                "exhibitors": {
                    "totalCount": 4,
                    "pageInfo": {
                        "hasNextPage": False,
                        "endCursor": None
                    },
                    "nodes": [
                        {
                            "_id": "Ex3",
                            "name": "Company Three",
                            "websiteUrl": "https://company-three.com",
                            "address": {"country": "Japan", "city": "Tokyo"},
                            "withEvent": {"booths": [{"name": "3C30"}]}
                        },
                        {
                            "_id": "Ex4",
                            "name": "Company Four",
                            "websiteUrl": "https://company-four.com",
                            "address": {"country": "Italy", "city": "Milan"},
                            "withEvent": {"booths": [{"name": "4D40"}]}
                        }
                    ]
                }
            }
        }
    }

    call_count = 0

    def mock_fetch(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 200
        if call_count == 1:
            resp.json.return_value = page1_data
        else:
            resp.json.return_value = page2_data
        return resp

    with patch("app.agents.universal_scraper.fetch_url", side_effect=mock_fetch):
        success = _scrape_swapcard_directory(url, mock_profile, result, budget)

    assert success is True
    assert result.strategy == "GRAPHQL_API"
    assert len(result.records) == 4
    assert result.records[0].company_name == "Company One"
    assert result.records[0].booth == "1A10"
    assert result.records[1].company_name == "Company Two"
    assert result.records[2].company_name == "Company Three"
    assert result.records[3].company_name == "Company Four"
    assert result.pages_scraped == 2


def test_normalize_record_country_inference():
    rec = normalize_record(
        ExhibitorRecord(
            company_name="Acme International",
            address="United States",
            country=None,
            source_url="https://example.com"
        )
    )
    assert rec.country == "United States"

    rec2 = normalize_record(
        ExhibitorRecord(
            company_name="Euro Labs",
            address="Via Roma 10, Milan, Italy",
            country=None,
            source_url="https://example.com"
        )
    )
    assert rec2.country == "Italy"
