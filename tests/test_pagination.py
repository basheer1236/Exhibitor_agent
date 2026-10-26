from app.discovery.pagination import detect_pagination


def test_page_pagination():

    url = "https://example.com/exhibitors?page=1"

    links = [
        "https://example.com/exhibitors?page=2",
        "https://example.com/exhibitors?page=3"
    ]

    result = detect_pagination(
        url,
        links
    )

    assert result["has_pagination"] is True


def test_no_pagination():

    url = "https://example.com/exhibitors"

    links = [
        "https://example.com/about",
        "https://example.com/contact"
    ]

    result = detect_pagination(
        url,
        links
    )

    assert result["has_pagination"] is False


def test_path_pagination_detection():
    url = "https://hochiminhexport.com/exhibitor-directory/?utm_source=chatgpt.com"
    links = [
        "https://hochiminhexport.com/exhibitor-directory/page/2/",
        "https://hochiminhexport.com/exhibitor-directory/page/3/",
        "https://hochiminhexport.com/exhibitor-directory/page/22/",
    ]

    result = detect_pagination(url, links)
    assert result["has_pagination"] is True
    assert len(result["signals"]) >= 1
    sig = result["signals"][0]
    assert sig["type"] == "path_parameter"
    assert sig["max_page"] == 22
    assert sig["total_pages"] == 22
    assert "{page}" in sig["path_pattern"]


def test_a2z_js_paginator_detection():
    url = "https://portal2026.semiconindia.org/exhibitors"
    html = """
    <html>
      <script>
        var ajaxParams = {
            'module':'organizations_organization_list',
            'limit':'50',
            'offset':'50'
        };
        jQuery(document).ready(function() {
            jQuery('#paginator').jsPaginator(ajaxParams, { paginationType:'std' }, 547);
        });
      </script>
    </html>
    """
    result = detect_pagination(url, [], html=html)
    assert result["has_pagination"] is True
    assert len(result["signals"]) >= 1
    sig = result["signals"][0]
    assert sig["parameter"] == "offset"
    assert sig["step"] == 50
    assert sig["total_count"] == 547
    assert sig["total_pages"] == 11
