from app.extraction.pagination_executor import build_page_url


def test_build_page_url():

    url = "https://example.com/exhibitors?page=1"

    result = build_page_url(
        url,
        "page",
        2
    )

    assert result == (
        "https://example.com/exhibitors?page=2"
    )


def test_build_page_url_without_existing_parameter():

    url = "https://example.com/exhibitors"

    result = build_page_url(
        url,
        "page",
        3
    )

    assert result == (
        "https://example.com/exhibitors?page=3"
    )


def test_build_page_url_path_pattern():
    url = "https://hochiminhexport.com/exhibitor-directory/?utm_source=chatgpt.com"
    result = build_page_url(
        url,
        parameter="path:/exhibitor-directory/page/{page}/",
        value=5,
    )
    assert result == "https://hochiminhexport.com/exhibitor-directory/page/5/?utm_source=chatgpt.com"


def test_build_page_url_explicit_path_pattern():
    url = "https://example.com/directory"
    result = build_page_url(
        url,
        parameter="any",
        value=2,
        path_pattern="/directory/p/{page}"
    )
    assert result == "https://example.com/directory/p/2"