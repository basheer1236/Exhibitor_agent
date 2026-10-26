"""
Comprehensive security and regression tests for Phase 5.2:
- Socket-level IP pinning
- Physical peer IP verification (getpeername match)
- DNS rebinding mitigation
- Pre-send verification on reused pool connections
- Cross-origin sensitive header stripping
- Streaming response size cap enforcement
- Bounded redirect revalidation
- TLS SNI and hostname verification preservation
- Socket evidence collection
"""

import ipaddress
import socket
import ssl
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
import urllib3

from app.discovery.http import (
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_MAX_RESPONSE_SIZE,
    InstrumentedHTTPConnection,
    InstrumentedHTTPSConnection,
    PinnedHTTPConnectionPool,
    PinnedHTTPSConnectionPool,
    PinnedPoolManager,
    SafeDNSAdapter,
    SafeSession,
    SocketEvidence,
    _verify_socket_peer,
    fetch_url,
    FetchResponse,
    HTTPClientError,
    HTTPTimeoutError,
    ResponseTooLargeError,
)
from app.guardrails.url import (
    SSRFError,
    SecurityError,
    TooManyRedirectsError,
)


# =====================================================================
# 1. Physical Peer Verification Unit Tests
# =====================================================================

class FakeSocket:
    """Mock socket providing getpeername, getsockname, and socket options."""
    def __init__(self, peer_ip: str, peer_port: int = 443, local_ip: str = "192.168.1.100", local_port: int = 54321):
        self.peer_ip = peer_ip
        self.peer_port = peer_port
        self.local_ip = local_ip
        self.local_port = local_port
        self.closed = False

    def getpeername(self):
        if self.closed:
            raise OSError("Socket closed")
        return (self.peer_ip, self.peer_port)

    def getsockname(self):
        return (self.local_ip, self.local_port)

    def setsockopt(self, *args, **kwargs):
        pass

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        pass

    def makefile(self, *args, **kwargs):
        import io
        return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")

    def close(self):
        self.closed = True


def test_verify_socket_peer_exact_ipv4_match():
    sock = FakeSocket("93.184.216.34", 80)
    peer_ip, peer_port = _verify_socket_peer(sock, "93.184.216.34")
    assert peer_ip == "93.184.216.34"
    assert peer_port == 80


def test_verify_socket_peer_ipv6_canonical_equivalence():
    # Test equivalent representations of 2606:4700::1
    sock = FakeSocket("2606:4700:0000:0000:0000:0000:0000:0001", 443)
    peer_ip, peer_port = _verify_socket_peer(sock, "2606:4700::1")
    assert peer_ip == "2606:4700::1"
    assert peer_port == 443


def test_verify_socket_peer_mismatch_raises_ssrf():
    sock = FakeSocket("192.168.1.1", 80)
    with pytest.raises(SSRFError, match="Physical peer mismatch"):
        _verify_socket_peer(sock, "93.184.216.34")


def test_verify_socket_peer_malformed_peer_raises_ssrf():
    sock = FakeSocket("not-an-ip", 80)
    with pytest.raises(SSRFError, match="Socket peer verification failed closed"):
        _verify_socket_peer(sock, "93.184.216.34")


def test_verify_socket_peer_malformed_pinned_raises_ssrf():
    sock = FakeSocket("93.184.216.34", 80)
    with pytest.raises(SSRFError, match="Socket peer verification failed closed"):
        _verify_socket_peer(sock, "not-an-ip")


def test_verify_socket_peer_none_or_closed_socket_raises_ssrf():
    with pytest.raises(SSRFError, match="Socket is closed or uninitialized"):
        _verify_socket_peer(None, "93.184.216.34")

    sock = FakeSocket("93.184.216.34", 80)
    sock.close()
    with pytest.raises(SSRFError, match="Socket peer verification failed closed"):
        _verify_socket_peer(sock, "93.184.216.34")


# =====================================================================
# 2. Instrumented Connection & IP Pinning Tests
# =====================================================================

def test_instrumented_http_connection_dials_pinned_ip_directly():
    conn = InstrumentedHTTPConnection(
        host="example.com",
        port=80,
        pinned_ip="93.184.216.34",
    )
    assert conn.pinned_ip == "93.184.216.34"

    mock_sock = FakeSocket("93.184.216.34", 80)
    with patch("socket.create_connection", return_value=mock_sock) as mock_create_conn:
        sock = conn._new_conn()
        mock_create_conn.assert_called_once_with(
            ("93.184.216.34", 80),
            conn.timeout,
        )
        assert sock == mock_sock
        assert conn.observed_peer == ("93.184.216.34", 80)


def test_instrumented_http_connection_missing_pin_fails_closed():
    conn = InstrumentedHTTPConnection(
        host="example.com",
        port=80,
        pinned_ip=None,
    )
    with pytest.raises(SSRFError, match="missing destination IP pin"):
        conn._new_conn()


def test_instrumented_http_connection_peer_mismatch_closes_socket():
    conn = InstrumentedHTTPConnection(
        host="example.com",
        port=80,
        pinned_ip="93.184.216.34",
    )
    # Dialed socket actually connected to a rogue IP (e.g. NAT rewrite or rebinding)
    rogue_sock = FakeSocket("10.0.0.1", 80)
    with patch("socket.create_connection", return_value=rogue_sock):
        with pytest.raises(SSRFError, match="Physical peer mismatch"):
            conn._new_conn()
        assert rogue_sock.closed is True


def test_instrumented_https_connection_preserves_sni_and_hostname_verification():
    conn = InstrumentedHTTPSConnection(
        host="example.com",
        port=443,
        pinned_ip="93.184.216.34",
    )
    assert conn.pinned_ip == "93.184.216.34"
    # SNI must remain the domain name, NOT the IP literal
    assert conn._tunnel_host is None
    assert conn.host == "example.com"


# =====================================================================
# 3. Connection Pool Pre-Send Verification Tests
# =====================================================================

def test_pinned_connection_pool_pre_send_verification_success():
    pool = PinnedHTTPConnectionPool(
        host="example.com",
        port=80,
        pinned_ip="93.184.216.34",
    )
    mock_conn = MagicMock()
    mock_conn.sock = FakeSocket("93.184.216.34", 80)
    mock_conn.is_closed = False
    mock_conn.observed_peer = ("93.184.216.34", 80)
    mock_conn.observed_local = ("192.168.1.100", 54321)
    mock_conn.request_count = 0

    mock_resp = MagicMock()
    with patch.object(urllib3.connectionpool.HTTPConnectionPool, "_make_request", return_value=mock_resp) as mock_super:
        res = pool._make_request(mock_conn, "GET", "/test")
        mock_super.assert_called_once_with(mock_conn, "GET", "/test")
        assert res._captured_peer == ("93.184.216.34", 80)


def test_pinned_connection_pool_pre_send_verification_detects_mutated_peer():
    pool = PinnedHTTPConnectionPool(
        host="example.com",
        port=80,
        pinned_ip="93.184.216.34",
    )
    mock_conn = MagicMock()
    # Socket peer changed to an unauthorized internal address on reused socket
    mock_conn.sock = FakeSocket("127.0.0.1", 80)
    mock_conn.is_closed = False

    with pytest.raises(SSRFError, match="Physical peer mismatch"):
        pool._make_request(mock_conn, "GET", "/test")


# =====================================================================
# 4. SafeDNSAdapter Request Dispatch Tests
# =====================================================================

def test_safe_dns_adapter_missing_pinned_ip_raises_ssrf():
    adapter = SafeDNSAdapter()
    req = requests.Request("GET", "https://example.com").prepare()
    # req._pinned_ip is omitted
    with pytest.raises(SSRFError, match="PreparedRequest lacks a validated destination IP pin"):
        adapter.send(req)


def test_safe_dns_adapter_disallowed_scheme_raises_security_error():
    adapter = SafeDNSAdapter()
    req = requests.Request("GET", "ftp://example.com/test").prepare()
    req._pinned_ip = "93.184.216.34"
    with pytest.raises(SecurityError, match="Unsupported scheme"):
        adapter.send(req)


# =====================================================================
# 5. DNS Rebinding Mitigation Test
# =====================================================================

def test_dns_rebinding_mitigation():
    """
    Simulates DNS rebinding:
    1. During resolve_and_validate_host(), domain resolves to validated public IP 93.184.216.34.
    2. Before or during socket connection, subsequent getaddrinfo returns private 127.0.0.1.
    3. The connection is explicitly pinned to 93.184.216.34 and does NOT query DNS again.
    """
    dialed_addresses = []

    def mock_create_connection(address, timeout=None, **kwargs):
        dialed_addresses.append(address)
        return FakeSocket(address[0], address[1])

    session = SafeSession()

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]), \
         patch("socket.create_connection", side_effect=mock_create_connection), \
         patch.object(requests.adapters.HTTPAdapter, "build_response") as mock_build_resp:

        mock_resp = requests.Response()
        mock_resp.status_code = 200
        mock_resp.raw = MagicMock()
        mock_resp.raw.read.return_value = b"OK"
        mock_resp.headers = {"Content-Length": "2"}
        mock_resp.iter_content = lambda chunk_size=8192: [b"OK"]
        mock_build_resp.return_value = mock_resp

        resp = session.get("http://rebinding-victim.com/data")
        assert len(dialed_addresses) == 1
        # Dialed target MUST be the validated pinned IP (93.184.216.34), NEVER the victim hostname
        assert dialed_addresses[0][0] == "93.184.216.34"
        assert dialed_addresses[0][1] == 80


# =====================================================================
# 6. Cross-Origin Sensitive Header Stripping Tests
# =====================================================================

def test_cross_origin_redirect_strips_sensitive_headers():
    session = SafeSession(max_redirects=5)

    recorded_headers_per_hop = []

    def fake_send(prep, **kwargs):
        recorded_headers_per_hop.append(dict(prep.headers))
        resp = requests.Response()
        resp.url = prep.url
        if prep.url == "https://origin-a.com/start":
            resp.status_code = 302
            resp.headers = {"Location": "https://origin-b.com/target"}
        else:
            resp.status_code = 200
            resp.headers = {"Content-Length": "2"}
            resp._content = b"OK"
        resp.raw = MagicMock()
        resp.raw.read.return_value = b"OK"
        resp.iter_content = lambda chunk_size=8192: [b"OK"]
        return resp

    session.session.send = fake_send

    with patch("app.discovery.http.resolve_and_validate_host", side_effect=[["93.184.216.34"], ["93.184.216.35"]]):
        resp = session.get(
            "https://origin-a.com/start",
            headers={
                "Authorization": "Bearer secret_token",
                "Cookie": "session_id=abcdef",
                "apikey": "super_secret_key",
                "Proxy-Authorization": "Basic 12345",
                "Accept": "application/json",
                "User-Agent": "UniversalExhibitorAgent/1.0",
            }
        )

    assert len(recorded_headers_per_hop) == 2
    hop1_headers = {k.lower(): v for k, v in recorded_headers_per_hop[0].items()}
    hop2_headers = {k.lower(): v for k, v in recorded_headers_per_hop[1].items()}

    # Hop 1 has all headers
    assert "authorization" in hop1_headers
    assert "cookie" in hop1_headers
    assert "apikey" in hop1_headers
    assert "proxy-authorization" in hop1_headers
    assert hop1_headers["accept"] == "application/json"

    # Hop 2 (cross-origin to origin-b.com) MUST strip sensitive headers
    assert "authorization" not in hop2_headers
    assert "cookie" not in hop2_headers
    assert "apikey" not in hop2_headers
    assert "proxy-authorization" not in hop2_headers

    # Hop 2 MUST preserve non-sensitive headers
    assert hop2_headers.get("accept") == "application/json"
    assert "universalexhibitoragent" in hop2_headers.get("user-agent", "").lower()


def test_same_origin_redirect_preserves_sensitive_headers():
    session = SafeSession(max_redirects=5)

    recorded_headers_per_hop = []

    def fake_send(prep, **kwargs):
        recorded_headers_per_hop.append(dict(prep.headers))
        resp = requests.Response()
        resp.url = prep.url
        if prep.url == "https://origin-a.com/start":
            resp.status_code = 302
            resp.headers = {"Location": "https://origin-a.com/next_page"}
        else:
            resp.status_code = 200
            resp.headers = {"Content-Length": "2"}
            resp._content = b"OK"
        resp.raw = MagicMock()
        resp.raw.read.return_value = b"OK"
        resp.iter_content = lambda chunk_size=8192: [b"OK"]
        return resp

    session.session.send = fake_send

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        resp = session.get(
            "https://origin-a.com/start",
            headers={
                "Authorization": "Bearer secret_token",
                "Cookie": "session_id=abcdef",
                "Accept": "application/json",
            }
        )

    assert len(recorded_headers_per_hop) == 2
    hop2_headers = {k.lower(): v for k, v in recorded_headers_per_hop[1].items()}
    # Same origin preserves authorization and cookie
    assert "authorization" in hop2_headers
    assert "cookie" in hop2_headers


# =====================================================================
# 7. Streaming Response Size Cap Enforcement Tests
# =====================================================================

def test_streaming_response_exceeding_max_bytes_raises_error():
    session = SafeSession(max_body_bytes=50)

    # Fake response that yields 60 bytes in chunks without Content-Length
    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com/stream"
    mock_resp.headers = {}
    mock_resp.iter_content = lambda chunk_size=8192: [b"A" * 30, b"B" * 30]
    mock_resp.close = MagicMock()

    session.session.send = MagicMock(return_value=mock_resp)

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        with pytest.raises(ResponseTooLargeError, match="exceeded limit of 50 bytes"):
            session.get("https://example.com/stream")

    assert mock_resp.close.called


def test_content_length_exceeding_max_bytes_aborts_immediately():
    session = SafeSession(max_body_bytes=100)

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com/large"
    mock_resp.headers = {"Content-Length": "5000"}
    mock_resp.close = MagicMock()

    session.session.send = MagicMock(return_value=mock_resp)

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        with pytest.raises(ResponseTooLargeError, match="Content-Length .* exceeds limit of 100 bytes"):
            session.get("https://example.com/large")

    assert mock_resp.close.called


# =====================================================================
# 8. Bounded Redirect Revalidation Tests
# =====================================================================

def test_redirect_to_prohibited_ssrf_destination_rejected_at_next_hop():
    session = SafeSession(max_redirects=5)

    def fake_send(prep, **kwargs):
        resp = requests.Response()
        resp.url = prep.url
        resp.status_code = 302
        # Redirect points to cloud metadata IP
        resp.headers = {"Location": "http://169.254.169.254/latest/meta-data"}
        resp.raw = MagicMock()
        return resp

    session.session.send = fake_send

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        with pytest.raises(SSRFError, match="Blocked unsafe IP address literal"):
            session.get("https://example.com/redirect_trap")


def test_redirect_chain_exceeding_max_redirects_raises_too_many_redirects():
    session = SafeSession(max_redirects=2)

    def fake_send(prep, **kwargs):
        resp = requests.Response()
        resp.url = prep.url
        resp.status_code = 302
        resp.headers = {"Location": f"{prep.url}/next"}
        resp.raw = MagicMock()
        return resp

    session.session.send = fake_send

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        with pytest.raises(TooManyRedirectsError, match="Exceeded maximum allowed redirects"):
            session.get("https://example.com/loop")


# =====================================================================
# 9. Socket Evidence & Chain Collection Tests
# =====================================================================

def test_socket_evidence_model_and_chain():
    evidence = SocketEvidence(
        request_id="req-123",
        expected_host="example.com",
        expected_pinned_ip="93.184.216.34",
        is_proxied=False,
        physical_peer_ip="93.184.216.34",
        physical_peer_port=443,
        physical_local_ip="192.168.1.50",
        physical_local_port=52000,
        connection_reused=False,
        tls_version="TLSv1.3",
        tls_cipher="TLS_AES_256_GCM_SHA384",
        verified=True,
        connected_at_epoch=1700000000.0,
    )
    d = evidence.to_dict()
    assert d["request_id"] == "req-123"
    assert d["verified"] is True
    assert d["expected_pinned_ip"] == "93.184.216.34"
    assert d["tls_version"] == "TLSv1.3"


def test_fetch_url_captures_socket_evidence():
    session = SafeSession()

    evidence = SocketEvidence(
        request_id="req-abc",
        expected_host="example.com",
        expected_pinned_ip="93.184.216.34",
        is_proxied=False,
        physical_peer_ip="93.184.216.34",
        physical_peer_port=443,
        physical_local_ip="192.168.1.50",
        physical_local_port=52000,
        connection_reused=False,
        tls_version=None,
        tls_cipher=None,
        verified=True,
        connected_at_epoch=1700000000.0,
    )

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com"
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp._content = b'{"status": "ok"}'
    mock_resp.socket_evidence = evidence
    mock_resp.raw = MagicMock()
    mock_resp.iter_content = lambda chunk_size=8192: [b'{"status": "ok"}']

    session.session.send = MagicMock(return_value=mock_resp)

    with patch("app.discovery.http.resolve_and_validate_host", return_value=["93.184.216.34"]):
        res = fetch_url("https://example.com", session=session)

    assert isinstance(res, FetchResponse)
    assert res.socket_evidence is not None
    assert res.socket_evidence["verified"] is True
    assert res.socket_evidence["expected_pinned_ip"] == "93.184.216.34"
    assert len(res.socket_evidence_chain) == 1
    assert res.json() == {"status": "ok"}
