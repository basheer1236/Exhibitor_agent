from app.extraction.repeated_records import (
    extract_repeated_records,
)


def test_extract_repeated_records():

    html = """
    <html>
        <body>

            <div class="card">
                <h3>ABC Technologies</h3>
                <a href="/profile/abc">
                    View Profile
                </a>
            </div>

            <div class="card">
                <h3>XYZ Solutions</h3>
                <a href="/profile/xyz">
                    View Profile
                </a>
            </div>

            <div class="card">
                <h3>DEF Industries</h3>
                <a href="/profile/def">
                    View Profile
                </a>
            </div>

        </body>
    </html>
    """

    records = extract_repeated_records(
        html,
        "https://example.com/exhibitors",
        "div.card",
    )

    assert len(records) == 3

    assert records[0].company_name == (
        "ABC Technologies"
    )

    assert records[0].detail_url == (
        "https://example.com/profile/abc"
    )

    assert records[1].company_name == (
        "XYZ Solutions"
    )

    assert records[2].company_name == (
        "DEF Industries"
    )