import pytest
import socket
import requests

from app.guardrails.url import (
    validate_url,
    resolve_and_validate_host,
    SecurityError,
    SSRFError,
    DNSResolutionError,
    TooManyRedirectsError,
    RestrictedPortError,
)
from app.discovery.http import (
    fetch_url,
    FetchResponse,
    HTTPTimeoutError,
    ResponseTooLargeError,
)
from app.agents.universal_scraper import scrape


# --------------------------------------------------
# Test Mock Response Helper
# --------------------------------------------------

class MockHTTPResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict | None = None,
        content_bytes: bytes = b"",
        text: str = "",
        url: str = "https://example.com",
    ):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = headers or {}
        self.content_bytes = content_bytes
        self.text = text or (content_bytes.decode("utf-8", errors="replace") if content_bytes else "")
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=8192):
        if not self.content_bytes:
            if self.text:
                data = self.text.encode("utf-8")
            else:
                data = b""
        else:
            data = self.content_bytes

        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if not (200 <= self.status_code < 400):
            raise requests.HTTPError(f"HTTP {self.status_code}")


# ==================================================
# A. URL Validation & SSRF Tests
# ==================================================

def test_url_validation_rejects_localhost():
    with pytest.raises(SecurityError):
        validate_url("http://localhost")
    with pytest.raises(SecurityError):
        validate_url("http://localhost:80")
    with pytest.raises(SecurityError):
        validate_url("http://sub.localhost")


def test_url_validation_rejects_loopback_ipv4():
    with pytest.raises(SecurityError):
        validate_url("http://127.0.0.1")
    with pytest.raises(SecurityError):
        validate_url("http://127.0.0.1:80")
    with pytest.raises(SecurityError):
        validate_url("http://127.255.0.1")


def test_url_validation_rejects_loopback_ipv6():
    with pytest.raises(SecurityError):
        validate_url("http://[::1]")
    with pytest.raises(SecurityError):
        validate_url("http://[::1]:80")


def test_url_validation_rejects_link_local_ipv4():
    with pytest.raises(SecurityError):
        validate_url("http://169.254.169.254")
    with pytest.raises(SecurityError):
        validate_url("http://169.254.1.1")


def test_url_validation_rejects_private_ipv4():
    with pytest.raises(SecurityError):
        validate_url("http://10.0.0.1")
    with pytest.raises(SecurityError):
        validate_url("http://172.16.0.1")
    with pytest.raises(SecurityError):
        validate_url("http://192.168.1.1")


def test_url_validation_rejects_private_and_link_local_ipv6():
    with pytest.raises(SecurityError):
        validate_url("http://[fc00::1]")
    with pytest.raises(SecurityError):
        validate_url("http://[fe80::1]")


def test_url_validation_rejects_internal_hostnames():
    with pytest.raises(SecurityError):
        validate_url("http://server.local")
    with pytest.raises(SecurityError):
        validate_url("http://db.internal")
    with pytest.raises(SecurityError):
        validate_url("http://router.lan")
    with pytest.raises(SecurityError):
        validate_url("http://intranet.corp")


def test_url_validation_accepts_valid_public_urls():
    assert validate_url("https://example.com/exhibitors") == "https://example.com/exhibitors"
    assert validate_url("http://example.com:80/list") == "http://example.com:80/list"
    assert validate_url("https://example.com:443/list") == "https://example.com:443/list"


# ==================================================
# B. DNS Resolution Tests
# ==================================================

def test_dns_resolving_to_public_ip_accepted(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80))
        ]
    )
    ips = resolve_and_validate_host("public-host.com", 80)
    assert "93.184.216.34" in ips


def test_dns_resolving_to_private_ip_rejected(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.50", 80))
        ]
    )
    with pytest.raises(SSRFError):
        resolve_and_validate_host("evil-internal.com", 80)


def test_dns_resolving_to_mixed_ips_rejected(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 80)),
        ]
    )
    with pytest.raises(SSRFError):
        resolve_and_validate_host("mixed-dns.com", 80)


# ==================================================
# C. Redirect Security Tests
# ==================================================

def test_redirect_public_to_public_allowed(monkeypatch):
    call_log = []

    def mock_requests_get(url, *args, **kwargs):
        call_log.append(url)
        if url == "https://example.com/start":
            return MockHTTPResponse(
                status_code=302,
                headers={"Location": "https://example.com/final"},
                url=url,
            )
        return MockHTTPResponse(
            status_code=200,
            text="<html>Final Content</html>",
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    res = fetch_url("https://example.com/start")
    assert isinstance(res, FetchResponse)
    assert res.status_code == 200
    assert res.redirects_followed == 1
    assert "Final Content" in res.content
    assert call_log == ["https://example.com/start", "https://example.com/final"]


def test_redirect_public_to_localhost_rejected(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockHTTPResponse(
            status_code=302,
            headers={"Location": "http://localhost:80/admin"},
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(SecurityError):
        fetch_url("https://example.com/start")


def test_redirect_public_to_private_ip_rejected(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockHTTPResponse(
            status_code=302,
            headers={"Location": "http://192.168.1.1/setup"},
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(SecurityError):
        fetch_url("https://example.com/start")


def test_excessive_redirects_rejected(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockHTTPResponse(
            status_code=302,
            headers={"Location": "https://example.com/loop"},
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(TooManyRedirectsError):
        fetch_url("https://example.com/loop", max_redirects=3)


# ==================================================
# D. Port Policy Tests
# ==================================================

def test_port_policy_accepted():
    assert validate_url("http://example.com:80") == "http://example.com:80"
    assert validate_url("https://example.com:443") == "https://example.com:443"
    assert validate_url("http://example.com:8080") == "http://example.com:8080"
    assert validate_url("https://example.com:8443") == "https://example.com:8443"


def test_port_policy_rejected():
    with pytest.raises(RestrictedPortError):
        validate_url("http://example.com:22")
    with pytest.raises(RestrictedPortError):
        validate_url("http://example.com:25")
    with pytest.raises(RestrictedPortError):
        validate_url("http://example.com:8000")
    with pytest.raises(RestrictedPortError):
        validate_url("http://example.com:3306")


# ==================================================
# E. Response Size Limit Tests
# ==================================================

def test_content_length_larger_than_limit_rejected(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockHTTPResponse(
            status_code=200,
            headers={"Content-Length": "10000000"},  # 10 MB
            text="Header oversize",
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(ResponseTooLargeError):
        fetch_url("https://example.com/huge", max_size_bytes=1000)


def test_streamed_response_exceeding_limit_rejected(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        # 10 chunks of 500 bytes = 5000 bytes
        big_content = b"X" * 5000
        return MockHTTPResponse(
            status_code=200,
            content_bytes=big_content,
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(ResponseTooLargeError):
        fetch_url("https://example.com/chunked", max_size_bytes=2000)


def test_response_under_limit_accepted(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        return MockHTTPResponse(
            status_code=200,
            content_bytes=b"Hello World Safe",
            url=url,
        )

    monkeypatch.setattr("requests.get", mock_requests_get)

    res = fetch_url("https://example.com/safe", max_size_bytes=1000)
    assert res.content == "Hello World Safe"
    assert res.bytes_read == len(b"Hello World Safe")


# ==================================================
# F. Timeout Tests
# ==================================================

def test_timeout_converted_into_controlled_error(monkeypatch):
    def mock_requests_get(url, *args, **kwargs):
        raise requests.Timeout("Connection timed out")

    monkeypatch.setattr("requests.get", mock_requests_get)

    with pytest.raises(HTTPTimeoutError):
        fetch_url("https://example.com/slow")


def test_timeout_in_scraper_captured_gracefully(monkeypatch):
    def mock_fetch_url(url, *args, **kwargs):
        raise HTTPTimeoutError("Timed out after 30 seconds")

    monkeypatch.setattr("app.discovery.site.fetch_url", mock_fetch_url)

    result = scrape("https://example.com/slow")
    assert result.success is False
    assert any("Timed out" in err for err in result.errors)
