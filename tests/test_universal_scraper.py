from app.agents.universal_scraper import scrape


def test_universal_scraper(monkeypatch):

    html = """
    <html>
        <body>

            <a href="/exhibitor/acme">
                ACME Industries
            </a>

            <a href="/exhibitor/beta">
                Beta Technologies
            </a>

        </body>
    </html>
    """

    def fake_fetch_url(url):

        return {
            "url": url,
            "status_code": 200,
            "headers": {
                "Content-Type": "text/html"
            },
            "content": html,
        }

    monkeypatch.setattr(
        "app.discovery.http.fetch_url",
        fake_fetch_url
    )

    monkeypatch.setattr(
        "app.discovery.site.fetch_url",
        fake_fetch_url
    )

    monkeypatch.setattr(
        "app.agents.universal_scraper.fetch_url",
        fake_fetch_url
    )

    result = scrape(
        "https://example.com/exhibitors"
    )

    print("STRATEGY:", result.strategy)
    print("ERRORS:", result.errors)

    assert result.success is True

    assert len(result.records) == 2

    assert result.records[0].company_name == (
        "ACME Industries"
    )

    assert result.records[1].company_name == (
        "Beta Technologies"
    )

    assert result.strategy == "HTML_DETAIL"