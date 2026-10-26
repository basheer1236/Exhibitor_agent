from app.extraction.deduplicator import (
    deduplicate_records,
)
from app.models.exhibitor import ExhibitorRecord


def test_deduplicate_by_detail_url():

    records = [
        ExhibitorRecord(
            company_name="ABC Technologies",
            detail_url="https://example.com/abc",
            source_url="https://example.com",
        ),
        ExhibitorRecord(
            company_name="ABC Technologies",
            detail_url="https://example.com/abc",
            source_url="https://example.com?page=2",
        ),
        ExhibitorRecord(
            company_name="XYZ Solutions",
            detail_url="https://example.com/xyz",
            source_url="https://example.com",
        ),
    ]

    result = deduplicate_records(records)

    assert len(result) == 2


def test_deduplicate_by_company_name():

    records = [
        ExhibitorRecord(
            company_name="ABC Technologies",
            source_url="https://example.com",
        ),
        ExhibitorRecord(
            company_name="ABC Technologies",
            source_url="https://example.com?page=2",
        ),
        ExhibitorRecord(
            company_name="XYZ Solutions",
            source_url="https://example.com",
        ),
    ]

    result = deduplicate_records(records)

    assert len(result) == 2