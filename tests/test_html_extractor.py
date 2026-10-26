from app.extraction.html_extractor import (
    extract_candidate_records,
    deduplicate_candidates
)


HTML = """
<html>
<body>

<a href="/exhibitors/abc">
    ABC Technologies
</a>

<a href="/exhibitors/xyz">
    XYZ Solutions
</a>

<a href="/about">
    About Us
</a>

<a href="/exhibitors/abc">
    ABC Technologies
</a>

</body>
</html>
"""


def test_candidate_extraction():

    records = extract_candidate_records(
        HTML,
        "https://example.com"
    )

    assert len(records) == 3


def test_deduplication():

    records = extract_candidate_records(
        HTML,
        "https://example.com"
    )

    unique = deduplicate_candidates(records)

    assert len(unique) == 2