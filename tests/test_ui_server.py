"""
Unit tests for the Web UI server and Excel export engine.
Tests endpoints directly without requiring external HTTP client test packages.
"""

import io
import zipfile
import pytest

from app.ui.server import (
    JOB_STORE,
    ScrapeRequest,
    api_scrape,
    download_csv,
    download_excel,
    download_json,
    pipeline_stream,
    serve_dashboard,
)
from app.ui.excel_exporter import records_to_xlsx, records_to_csv
from fastapi import HTTPException


def test_excel_exporter_creates_valid_xlsx():
    records = [
        {
            "company_name": "Atlas Copco",
            "website": "https://atlascopco.com",
            "email": "contact@atlascopco.com",
            "emails": ["contact@atlascopco.com", "sales@atlascopco.com"],
            "phone": "+46 8 743 80 00",
            "booth": "Hall 4 / 412",
            "city": "Stockholm",
            "country": "Sweden",
        },
        {
            "company_name": "Bosch Rexroth",
            "website": "https://boschrexroth.com",
            "email": "info@boschrexroth.com",
            "booth": "Hall 1 / 105",
            "city": "Lohr am Main",
            "country": "Germany",
        }
    ]

    xlsx_bytes = records_to_xlsx(records)
    assert len(xlsx_bytes) > 500
    assert xlsx_bytes[:2] == b"PK"

    # Verify that the generated zip contains valid OpenXML structure
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
        file_list = zf.namelist()
        assert "[Content_Types].xml" in file_list
        assert "xl/workbook.xml" in file_list
        assert "xl/worksheets/sheet1.xml" in file_list
        assert "xl/styles.xml" in file_list

        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Atlas Copco" in sheet_xml
        assert "Bosch Rexroth" in sheet_xml
        assert "Hall 4 / 412" in sheet_xml


def test_csv_exporter_creates_valid_csv():
    records = [
        {"company_name": "Test Company", "website": "https://test.com", "email": "test@test.com"}
    ]
    csv_str = records_to_csv(records)
    lines = csv_str.strip().splitlines()
    assert len(lines) == 2
    assert "Company Name" in lines[0]
    assert "Test Company" in lines[1]
    assert "https://test.com" in lines[1]


@pytest.mark.anyio
async def test_dashboard_endpoint_serves_html():
    response = await serve_dashboard()
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html_text = response.body.decode("utf-8")
    assert "Universal Exhibitor Agent" in html_text
    assert "Run Scraper" in html_text
    assert "Download Excel (.xlsx)" in html_text


@pytest.mark.anyio
async def test_pipeline_stream_endpoint():
    response = await pipeline_stream()
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.anyio
async def test_scrape_api_validation_rejects_invalid_url():
    with pytest.raises(HTTPException) as exc_info:
        await api_scrape(ScrapeRequest(url="ftp://bad-scheme.com"))
    assert exc_info.value.status_code == 400
    assert "URL must start with http://" in exc_info.value.detail


@pytest.mark.anyio
async def test_download_endpoints_with_mock_job():
    test_job_id = "test-job-999"
    JOB_STORE[test_job_id] = {
        "job_id": test_job_id,
        "url": "https://expo-directory.org",
        "domain_slug": "expo_directory_org",
        "created_at": "2026-10-25T12:00:00Z",
        "success": True,
        "strategy": "API",
        "records_count": 1,
        "pages_scraped": 1,
        "requests_made": 2,
        "duration_seconds": 1.5,
        "quality_status": "COMPLETE",
        "quality_score": 0.95,
        "errors": [],
        "records": [
            {
                "company_name": "Apex Engineering",
                "website": "https://apex.com",
                "email": "apex@apex.com",
                "booth": "B-10",
                "hall": "1",
                "city": "Chicago",
                "country": "USA",
            }
        ]
    }

    # 1. Download Excel
    resp_xlsx = await download_excel(job_id=test_job_id)
    assert resp_xlsx.status_code == 200
    assert resp_xlsx.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment; filename=" in resp_xlsx.headers["Content-Disposition"]
    assert resp_xlsx.body[:2] == b"PK"

    # 2. Download CSV
    resp_csv = await download_csv(job_id=test_job_id)
    assert resp_csv.status_code == 200
    assert resp_csv.media_type == "text/csv; charset=utf-8"
    assert "Apex Engineering" in resp_csv.body.decode("utf-8-sig")

    # 3. Download JSON
    resp_json = await download_json(job_id=test_job_id)
    assert resp_json.status_code == 200
    assert resp_json.media_type == "application/json; charset=utf-8"
    assert b'"job_id": "test-job-999"' in resp_json.body


@pytest.mark.anyio
async def test_download_endpoints_404_for_unknown_job():
    with pytest.raises(HTTPException) as exc_info:
        await download_excel(job_id="unknown-id")
    assert exc_info.value.status_code == 404
