from app.discovery.api import find_api_candidates


HTML = """
<html>

<script>
    fetch("/api/v1/exhibitors")
</script>

<script>
    const url = "/api/v2/companies";
</script>

<script>
    fetch("/graphql")
</script>

<a href="/about">About</a>

</html>
"""


def test_api_discovery():

    results = find_api_candidates(
        HTML,
        "https://example.com/exhibitors/"
    )

    assert "https://example.com/api/v1/exhibitors" in results

    assert "https://example.com/api/v2/companies" in results

    assert "https://example.com/graphql" in results


def test_ajax_concatenated_handler_discovery():
    html_ajax = """
    <script>
    function BindExhibitorList(page) {
        var query = $('#txtAlphabet').val() + '~' + $('#textboxSearch').val();
        $.getJSON("exhibitorlisthandler2025.ashx?pageIndex=" + page + "&pageSize=12&q=" + query, function(data) {
            console.log(data);
        });
    }
    </script>
    """
    results = find_api_candidates(html_ajax, "https://example.com/exhibitors")
    assert any("exhibitorlisthandler2025.ashx?pageIndex=1&pageSize=12" in u for u in results)


def test_pascal_case_api_normalization():
    from app.extraction.normalizer import normalize_api_record

    raw_item = {
        "EventId": "1",
        "ExhibitorId": "10028706",
        "StandNo": "BM14",
        "ProfileImage": "https://example.com/logo.png",
        "ExhibitorTier": "11",
        "CompanyName": "A.C. HUMIDIN AIR SYSTEMS PVT LTD",
        "Country": "INDIA",
    }
    rec = normalize_api_record(raw_item, "https://example.com/api/list")
    assert rec.company_name == "A.C. HUMIDIN AIR SYSTEMS PVT LTD"
    assert rec.booth == "BM14"
    assert rec.hall == "11"
    assert rec.country == "INDIA"
    assert rec.exhibitor_id == "10028706"


def test_bogus_navigation_names_rejected():
    from app.extraction.html_records import _is_valid_company_name

    bogus_names = [
        "Exhibitor Feedback",
        "Exhibitor Profiles 2025",
        "Exhibitor List 2025",
        "Become an Exhibitor",
        "Bharat Mobility 2024 Exhibitor Directory",
        "Exhibitor Enquiry",
        "Visitor Registration",
        "Why Exhibit at Bharat Mobility 2027",
        "Book Your Space",
        "Reserve Your Stand",
        "Get A Pod",
        "Exhibitors & products",
        "Take part in Transport + Traffic Infratech Expo now",
    ]
    for name in bogus_names:
        assert not _is_valid_company_name(name), f"Expected '{name}' to be rejected as company name"


def test_data_config_api_discovery():
    html_config = """
    <div class="exhibitorSearch abstractComponent">
        <div data-config='{"API_URL":"https://exhibitorsearch.messefrankfurt.com/service","API_EVENT_ID":"TRANSPORTTRAFFICINFRATECHEXPO","LANGUAGE":"en-GB","PAGE_SIZE":25}' id="mf-ex-root"></div>
    </div>
    """
    cands = find_api_candidates(html_config, "https://trafficinfratechexpo.in.messefrankfurt.com/newdelhi/en/exhibitor-search.html")
    assert any("findEventVariable=TRANSPORTTRAFFICINFRATECHEXPO" in c for c in cands)
    assert any("messefrankfurt.com" in c for c in cands)