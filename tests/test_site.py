from unittest.mock import patch
from app.discovery.site import discover_site
from app.models.site_profile import SiteProfile

HTML = """
<html>
<body>

<a href="/exhibitor/acme">
    ACME Industries
</a>

<a href="/exhibitor/beta">
    Beta Technologies
</a>

<a href="/exhibitors?page=2">
    Next
</a>

</body>
</html>
"""

def test_discover_site(monkeypatch):
    def fake_fetch_url(url):
        return {
            "url": url,
            "status_code": 200,
            "headers": {
                "Content-Type": "text/html"
            },
            "content": HTML,
        }

    monkeypatch.setattr("app.discovery.site.fetch_url", fake_fetch_url)

    profile = discover_site("https://example.com/exhibitors")

    assert profile.status_code == 200
    assert profile.is_html is True
    assert profile.has_detail_links is True
    assert profile.has_pagination is True
    assert profile.site_type == "HTML_DIRECTORY"
    
    # detail links are detected, at least one expected detail URL is present
    assert "https://example.com/exhibitor/acme" in profile.detail_links
