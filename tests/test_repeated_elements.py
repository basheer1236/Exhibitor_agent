from app.discovery.repeated_elements import (
    find_repeated_elements
)


HTML = """
<html>
<body>

<div class="card">
    <h3>ABC Technologies</h3>
</div>

<div class="card">
    <h3>XYZ Industries</h3>
</div>

<div class="card">
    <h3>DEF Solutions</h3>
</div>

<div class="footer">
    Footer
</div>

</body>
</html>
"""


def test_repeated_cards():

    results = find_repeated_elements(HTML)

    signatures = [
        item["signature"]
        for item in results
    ]

    assert "div.card" in signatures