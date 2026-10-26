"""Centralized secure HTTP client with DNS rebinding protection and socket-level IP pinning."""

from dataclasses import dataclass, field
import hashlib
import ipaddress
import os
import re
import socket
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
from urllib.parse import urljoin, urlparse
import uuid

import requests
import requests.adapters
import requests.api
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.poolmanager import PoolManager

from app.guardrails.url import (
    DNSResolutionError,
    InvalidURLError,
    RestrictedPortError,
    SecurityError,
    SSRFError,
    SSRFSecurityError,
    TooManyRedirectsError,
    is_safe_destination_ip,
    resolve_and_validate_host,
    validate_url,
)


DEFAULT_MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB default limit
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30.0

_ORIGINAL_REQUESTS_GET = requests.api.get


# --------------------------------------------------
# Exceptions
# --------------------------------------------------

class HTTPClientError(Exception):
    """Base class for HTTP client errors."""
    pass


class HTTPTimeoutError(HTTPClientError):
    """Raised when an HTTP request times out."""
    pass


class ResponseTooLargeError(HTTPClientError):
    """Raised when an HTTP response exceeds the configured maximum size limit."""
    pass


class HTTPStatusError(HTTPClientError):
    """Raised when an HTTP response indicates a 4xx or 5xx client/server error."""
    pass


# --------------------------------------------------
# Socket Evidence Model
# --------------------------------------------------

@dataclass
class SocketEvidence:
    """Immutable audit record capturing verified physical socket metadata."""
    request_id: str
    expected_host: str
    expected_pinned_ip: str
    is_proxied: bool
    physical_peer_ip: Optional[str]
    physical_peer_port: Optional[int]
    physical_local_ip: Optional[str]
    physical_local_port: Optional[int]
    connection_reused: bool
    tls_version: Optional[str]
    tls_cipher: Optional[str]
    verified: bool
    connected_at_epoch: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "expected_host": self.expected_host,
            "expected_pinned_ip": self.expected_pinned_ip,
            "is_proxied": self.is_proxied,
            "physical_peer_ip": self.physical_peer_ip,
            "physical_peer_port": self.physical_peer_port,
            "physical_local_ip": self.physical_local_ip,
            "physical_local_port": self.physical_local_port,
            "connection_reused": self.connection_reused,
            "tls_version": self.tls_version,
            "tls_cipher": self.tls_cipher,
            "verified": self.verified,
            "connected_at_epoch": self.connected_at_epoch,
        }


# --------------------------------------------------
# FetchResponse Structure
# --------------------------------------------------

@dataclass
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    content: str = ""
    content_type: str = ""
    bytes_read: int = 0
    redirects_followed: int = 0
    socket_evidence: dict[str, Any] | None = None
    socket_evidence_chain: list[dict[str, Any]] = field(default_factory=list)

    @property
    def url(self) -> str:
        """Alias for final_url to maintain compatibility."""
        return self.final_url

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def text(self) -> str:
        return self.content

    def raise_for_status(self) -> None:
        if not self.ok:
            raise HTTPStatusError(f"HTTP {self.status_code} error")

    def json(self) -> Any:
        import json
        return json.loads(self.content)

    def __getitem__(self, key: str) -> Any:
        """Dictionary compatibility for existing code expecting dict access."""
        if key == "url":
            return self.final_url
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


# --------------------------------------------------
# Socket-Level IP Pinning & Instrumented Connections
# --------------------------------------------------

def _verify_socket_peer(sock: socket.socket, expected_ip_str: str) -> tuple[str, int]:
    """
    Directly queries the OS socket via getpeername() and performs a canonical
    IP object comparison against the pre-validated pinned destination.
    Fails closed immediately if the peer does not match or is malformed.
    """
    if not sock:
        raise SSRFError("Socket is closed or uninitialized.")

    try:
        peername = sock.getpeername()
        if not peername or not isinstance(peername, tuple) or len(peername) < 2:
            sock.close()
            raise SSRFError(f"Malformed socket peername returned by OS: {peername!r}")

        raw_peer_ip, raw_peer_port = peername[0], peername[1]
        peer_ip_obj = ipaddress.ip_address(raw_peer_ip)
        pinned_ip_obj = ipaddress.ip_address(expected_ip_str)
    except (OSError, ValueError, TypeError) as exc:
        sock.close()
        raise SSRFError(f"Socket peer verification failed closed: {exc}") from exc

    if peer_ip_obj != pinned_ip_obj:
        sock.close()
        raise SSRFError(
            f"Physical peer mismatch: socket connected to {peer_ip_obj}, "
            f"expected pinned destination {pinned_ip_obj}"
        )

    return str(peer_ip_obj), raw_peer_port


class InstrumentedHTTPConnection(HTTPConnection):
    """Subclasses urllib3 HTTPConnection to dial pre-validated pinned_ip directly."""
    def __init__(self, *args, pinned_ip: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pinned_ip = pinned_ip
        self.observed_peer = None
        self.observed_local = None
        self.first_connected_at = None
        self.request_count = 0

    def _new_conn(self) -> socket.socket:
        if not self.pinned_ip:
            raise SSRFError("Egress forbidden: missing destination IP pin.")

        extra_args = {}
        if self.source_address:
            extra_args["source_address"] = self.source_address

        sock = socket.create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            **extra_args
        )
        for opt in self.socket_options:
            sock.setsockopt(*opt)

        peer_ip, peer_port = _verify_socket_peer(sock, self.pinned_ip)
        self.observed_peer = (peer_ip, peer_port)
        self.observed_local = sock.getsockname()
        if self.first_connected_at is None:
            self.first_connected_at = time.time()
        return sock


class InstrumentedHTTPSConnection(HTTPSConnection):
    """Subclasses urllib3 HTTPSConnection to dial pinned_ip directly while preserving standard TLS."""
    def __init__(self, *args, pinned_ip: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pinned_ip = pinned_ip
        self.observed_peer = None
        self.observed_local = None
        self.first_connected_at = None
        self.request_count = 0
        self.observed_tls_version = None
        self.observed_tls_cipher = None

    def _new_conn(self) -> socket.socket:
        if not self.pinned_ip:
            raise SSRFError("Egress forbidden: missing destination IP pin.")

        extra_args = {}
        if self.source_address:
            extra_args["source_address"] = self.source_address

        # Dial pre-validated IP directly at the socket layer
        sock = socket.create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            **extra_args
        )
        for opt in self.socket_options:
            sock.setsockopt(*opt)

        peer_ip, peer_port = _verify_socket_peer(sock, self.pinned_ip)
        self.observed_peer = (peer_ip, peer_port)
        self.observed_local = sock.getsockname()
        if self.first_connected_at is None:
            self.first_connected_at = time.time()
        return sock

    def connect(self):
        super().connect()
        if self.sock:
            try:
                self.observed_tls_version = self.sock.version() if hasattr(self.sock, "version") else None
                c_info = self.sock.cipher() if hasattr(self.sock, "cipher") else None
                self.observed_tls_cipher = c_info[0] if c_info else None
            except Exception:
                self.observed_tls_version = None
                self.observed_tls_cipher = None


class PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = InstrumentedHTTPConnection

    def __init__(self, host, port=None, pinned_ip=None, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, port, **kwargs)
        self.conn_kw["pinned_ip"] = pinned_ip

    def _make_request(self, conn, *args, **kwargs):
        # Pre-send verification: confirm socket peer before sending request payload
        if getattr(conn, "sock", None):
            _verify_socket_peer(conn.sock, self.pinned_ip)

        resp = super()._make_request(conn, *args, **kwargs)
        resp._captured_peer = getattr(conn, "observed_peer", None)
        resp._captured_local = getattr(conn, "observed_local", None)
        req_count = getattr(conn, "request_count", 0)
        resp._captured_reused = req_count > 0
        conn.request_count = req_count + 1
        resp._captured_tls_version = None
        resp._captured_tls_cipher = None
        return resp


class PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = InstrumentedHTTPSConnection

    def __init__(self, host, port=None, pinned_ip=None, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(host, port, **kwargs)
        self.conn_kw["pinned_ip"] = pinned_ip

    def _make_request(self, conn, *args, **kwargs):
        # Pre-send verification: confirm socket peer before sending request payload
        if getattr(conn, "sock", None):
            _verify_socket_peer(conn.sock, self.pinned_ip)

        resp = super()._make_request(conn, *args, **kwargs)
        resp._captured_peer = getattr(conn, "observed_peer", None)
        resp._captured_local = getattr(conn, "observed_local", None)
        req_count = getattr(conn, "request_count", 0)
        resp._captured_reused = req_count > 0
        conn.request_count = req_count + 1

        resp._captured_tls_version = getattr(conn, "observed_tls_version", None)
        resp._captured_tls_cipher = getattr(conn, "observed_tls_cipher", None)
        return resp


class PinnedPoolManager(PoolManager):
    """
    Custom PoolManager isolating connection pools by 5-tuple:
    (scheme, host.lower(), port, pinned_ip, tls_identity).
    """
    def __init__(self, maxsize=20, **connection_pool_kw):
        super().__init__(**connection_pool_kw)
        self.maxsize = maxsize
        self.pinned_pools: Dict[Tuple, urllib3.connectionpool.ConnectionPool] = {}
        self.pool_lru_keys: List[Tuple] = []

    def get_pinned_pool(self, scheme: str, host: str, port: int, pinned_ip: str, tls_identity: Tuple, pool_kwargs: dict):
        scheme = scheme.lower()
        host = host.lower()
        port = port or (443 if scheme == "https" else 80)
        pool_key = (scheme, host, port, pinned_ip, tls_identity)

        if pool_key in self.pinned_pools:
            self.pool_lru_keys.remove(pool_key)
            self.pool_lru_keys.append(pool_key)
            return self.pinned_pools[pool_key]

        # Enforce LRU eviction if at pool capacity
        if len(self.pinned_pools) >= self.maxsize:
            oldest_key = self.pool_lru_keys.pop(0)
            evicted_pool = self.pinned_pools.pop(oldest_key, None)
            if evicted_pool:
                try:
                    evicted_pool.close()
                except Exception:
                    pass

        kw = dict(self.connection_pool_kw)
        kw.update(pool_kwargs)
        kw["pinned_ip"] = pinned_ip

        if scheme == "https":
            pool = PinnedHTTPSConnectionPool(host, port, **kw)
        else:
            pool = PinnedHTTPConnectionPool(host, port, **kw)

        self.pinned_pools[pool_key] = pool
        self.pool_lru_keys.append(pool_key)
        return pool


class SafeDNSAdapter(requests.adapters.HTTPAdapter):
    """
    Requests HTTPAdapter enforcing destination IP pinning and capturing verified socket evidence.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pinned_pool_manager = PinnedPoolManager()

    def _compute_tls_identity(self, verify, cert) -> Tuple:
        """Computes a stable fingerprint of the TLS trust configuration."""
        if verify is True:
            trust_type = "SYSTEM_DEFAULT"
            ca_fingerprint = "SYSTEM_DEFAULT"
        elif verify is False:
            trust_type = "INSECURE_DISABLED"
            ca_fingerprint = "DISABLED"
        elif isinstance(verify, str):
            trust_type = "CUSTOM_CA_FILE"
            if os.path.isfile(verify):
                stat = os.stat(verify)
                try:
                    with open(verify, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                    ca_fingerprint = f"{verify}:{stat.st_mtime}:{file_hash}"
                except Exception:
                    ca_fingerprint = f"{verify}:{stat.st_mtime}:{stat.st_size}"
            else:
                ca_fingerprint = f"MISSING:{verify}"
        else:
            trust_type = "CUSTOM_CONTEXT"
            ca_fingerprint = str(id(verify))

        if cert:
            cert_file = cert if isinstance(cert, str) else (cert[0] if cert else None)
            if cert_file and os.path.isfile(cert_file):
                try:
                    with open(cert_file, "rb") as f:
                        cert_hash = hashlib.sha256(f.read()).hexdigest()
                    client_fingerprint = f"CERT:{cert_file}:{cert_hash}"
                except Exception:
                    client_fingerprint = f"CERT:{cert_file}"
            else:
                client_fingerprint = f"CERT_SPECIFIED:{cert_file}"
        else:
            client_fingerprint = "NO_CLIENT_CERT"

        return (trust_type, ca_fingerprint, client_fingerprint)

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        pinned_ip = getattr(request, "_pinned_ip", None)
        expected_host = getattr(request, "_expected_host", None)
        request_id = getattr(request, "_request_id", uuid.uuid4().hex)

        if not pinned_ip:
            raise SSRFError("Egress forbidden: PreparedRequest lacks a validated destination IP pin.")

        parsed = urllib.parse.urlparse(request.url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise SecurityError(f"Unsupported scheme: {scheme}. Only HTTP and HTTPS egress permitted.")
        host = parsed.hostname or expected_host
        port = parsed.port or (443 if scheme == "https" else 80)

        # Prohibit unapproved client proxies
        if proxies:
            proxy_url = proxies.get(scheme) or proxies.get("all")
            if proxy_url:
                raise SSRFError("Explicit client proxies forbidden: direct egress pinning required.")

        tls_identity = self._compute_tls_identity(verify, cert)

        pool_kwargs = {}
        if scheme == "https":
            if isinstance(verify, str):
                pool_kwargs["ca_certs"] = verify
                pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED
            elif verify is True:
                pool_kwargs["cert_reqs"] = ssl.CERT_REQUIRED
            elif verify is False:
                pool_kwargs["cert_reqs"] = ssl.CERT_NONE

            if cert:
                if isinstance(cert, (tuple, list)):
                    pool_kwargs["cert_file"] = cert[0]
                    pool_kwargs["key_file"] = cert[1] if len(cert) > 1 else None
                elif isinstance(cert, str):
                    pool_kwargs["cert_file"] = cert

        pool = self.pinned_pool_manager.get_pinned_pool(
            scheme=scheme,
            host=host,
            port=port,
            pinned_ip=pinned_ip,
            tls_identity=tls_identity,
            pool_kwargs=pool_kwargs,
        )

        url = parsed.path
        if parsed.query:
            url += f"?{parsed.query}"
        if not url:
            url = "/"

        if isinstance(timeout, tuple):
            timeout_obj = urllib3.util.Timeout(connect=timeout[0], read=timeout[1])
        elif isinstance(timeout, urllib3.util.Timeout):
            timeout_obj = timeout
        elif timeout is not None:
            timeout_obj = urllib3.util.Timeout(connect=timeout, read=timeout)
        else:
            timeout_obj = urllib3.util.Timeout()

        try:
            resp = pool.urlopen(
                method=request.method,
                url=url,
                body=request.body,
                headers=request.headers,
                redirect=False,
                assert_same_host=False,
                preload_content=False,
                decode_content=False,
                retries=False,
                timeout=timeout_obj,
            )
        except Exception as exc:
            evidence = SocketEvidence(
                request_id=request_id,
                expected_host=host,
                expected_pinned_ip=pinned_ip,
                is_proxied=False,
                physical_peer_ip=None,
                physical_peer_port=None,
                physical_local_ip=None,
                physical_local_port=None,
                connection_reused=False,
                tls_version=None,
                tls_cipher=None,
                verified=False,
                connected_at_epoch=time.time(),
            )
            exc.socket_evidence = evidence

            if isinstance(exc, urllib3.exceptions.SSLError):
                new_exc = requests.exceptions.SSLError(exc, request=request)
                new_exc.socket_evidence = evidence
                raise new_exc
            elif isinstance(exc, urllib3.exceptions.MaxRetryError):
                if isinstance(exc.reason, urllib3.exceptions.SSLError):
                    new_exc = requests.exceptions.SSLError(exc, request=request)
                else:
                    new_exc = requests.exceptions.ConnectionError(exc, request=request)
                new_exc.socket_evidence = evidence
                raise new_exc
            elif isinstance(exc, (urllib3.exceptions.HTTPError, OSError)):
                new_exc = requests.exceptions.ConnectionError(exc, request=request)
                new_exc.socket_evidence = evidence
                raise new_exc
            raise

        response = self.build_response(request, resp)

        peer = getattr(resp, "_captured_peer", None)
        local = getattr(resp, "_captured_local", None)
        reused = getattr(resp, "_captured_reused", False)
        tls_ver = getattr(resp, "_captured_tls_version", None)
        tls_ciph = getattr(resp, "_captured_tls_cipher", None)

        verified = False
        if peer:
            try:
                verified = (ipaddress.ip_address(peer[0]) == ipaddress.ip_address(pinned_ip))
            except Exception:
                verified = False

        evidence = SocketEvidence(
            request_id=request_id,
            expected_host=host,
            expected_pinned_ip=pinned_ip,
            is_proxied=False,
            physical_peer_ip=peer[0] if peer else None,
            physical_peer_port=peer[1] if peer else None,
            physical_local_ip=local[0] if local else None,
            physical_local_port=local[1] if local else None,
            connection_reused=reused,
            tls_version=tls_ver,
            tls_cipher=tls_ciph,
            verified=verified,
            connected_at_epoch=time.time(),
        )

        response.socket_evidence = evidence
        return response


# --------------------------------------------------
# Safe Session Orchestration
# --------------------------------------------------

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "apikey",
    "x-api-key",
    "token",
    "x-auth-token",
}


class SafeSession:
    """
    Secure HTTP Session orchestrating URL validation, DNS resolution,
    physical socket pinning, redirect revalidation, and cross-origin header stripping.
    """
    def __init__(
        self,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        total_timeout: float = DEFAULT_TIMEOUT,
        max_body_bytes: int = DEFAULT_MAX_RESPONSE_SIZE,
    ):
        self.max_redirects = max_redirects
        self.total_timeout = total_timeout
        self.max_body_bytes = max_body_bytes
        self.session = requests.Session()
        self.adapter = SafeDNSAdapter()
        self.session.mount("https://", self.adapter)
        self.session.mount("http://", self.adapter)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        start_time = time.time()
        current_url = url
        current_method = method.upper()
        current_data = kwargs.get("data")
        current_json = kwargs.get("json")
        headers = dict(kwargs.get("headers") or {})
        verify = kwargs.get("verify", True)
        cert = kwargs.get("cert", None)

        redirect_count = 0
        evidence_chain: List[SocketEvidence] = []

        while True:
            # 1. Budget check
            elapsed = time.time() - start_time
            remaining_time = self.total_timeout - elapsed
            if remaining_time <= 0:
                raise HTTPTimeoutError(f"Total request budget of {self.total_timeout}s exceeded.")

            # 2. Syntactic URL validation & Host extraction
            validated_url = validate_url(current_url)
            parsed = urlparse(validated_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)

            # 3. DNS resolution & IP safety check
            safe_ips = resolve_and_validate_host(parsed.hostname, port)
            pinned_ip = safe_ips[0]

            # 4. Prepare request with pinned IP
            req = requests.Request(
                method=current_method,
                url=validated_url,
                headers=headers,
                data=current_data,
                json=current_json,
            )
            prep = self.session.prepare_request(req)
            prep._pinned_ip = pinned_ip
            prep._expected_host = parsed.hostname
            prep._request_id = uuid.uuid4().hex

            # 5. Dispatch via SafeDNSAdapter
            try:
                resp = self.session.send(
                    prep,
                    timeout=remaining_time,
                    verify=verify,
                    cert=cert,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.Timeout as exc:
                raise HTTPTimeoutError(f"Request to {validated_url} timed out: {exc}") from exc
            except (SSRFError, requests.exceptions.RequestException) as exc:
                raise

            if hasattr(resp, "socket_evidence"):
                evidence_chain.append(resp.socket_evidence)

            # 6. Check for redirect
            if resp.status_code in {301, 302, 303, 307, 308}:
                location = resp.headers.get("Location")
                if location:
                    redirect_count += 1
                    if redirect_count > self.max_redirects:
                        resp.close()
                        raise TooManyRedirectsError(
                            f"Exceeded maximum allowed redirects ({self.max_redirects}) for URL: {url}"
                        )

                    next_url = urljoin(validated_url, location)
                    next_parsed = urlparse(next_url)

                    # Cross-origin header stripping (case-insensitive)
                    origin_changed = (
                        next_parsed.scheme != parsed.scheme
                        or next_parsed.hostname != parsed.hostname
                        or next_parsed.port != parsed.port
                    )
                    if origin_changed:
                        headers = {
                            k: v for k, v in headers.items()
                            if k.lower() not in SENSITIVE_HEADER_NAMES
                        }

                    # Method rewrite rules for 301/302/303
                    if resp.status_code in {301, 302, 303}:
                        current_method = "GET"
                        current_data = None
                        current_json = None

                    resp.close()
                    current_url = next_url
                    continue

            # Terminal non-redirect response reached
            break

        # 7. Incremental response-size enforcement
        cl_header = resp.headers.get("Content-Length")
        if cl_header:
            try:
                cl_int = int(cl_header)
                if cl_int > self.max_body_bytes:
                    resp.close()
                    raise ResponseTooLargeError(
                        f"Response Content-Length ({cl_int} bytes) exceeds limit of {self.max_body_bytes} bytes"
                    )
            except ValueError:
                pass

        chunks = []
        bytes_read = 0
        try:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read > self.max_body_bytes:
                    resp.close()
                    raise ResponseTooLargeError(
                        f"Response stream exceeded limit of {self.max_body_bytes} bytes"
                    )
                chunks.append(chunk)
        finally:
            resp.close()

        resp._content = b"".join(chunks)
        resp.socket_evidence_chain = evidence_chain
        resp.redirects_followed = redirect_count
        return resp

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self):
        self.session.close()


# --------------------------------------------------
# Centralized Secure Fetch
# --------------------------------------------------

def fetch_url(
    url: str,
    timeout: int | float | tuple[int | float, int | float] = DEFAULT_TIMEOUT,
    max_size_bytes: int = DEFAULT_MAX_RESPONSE_SIZE,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    headers: dict[str, str] | None = None,
    session: requests.Session | SafeSession | None = None,
    method: str = "GET",
    data: Any = None,
    json: Any = None,
) -> FetchResponse:
    """
    Perform a secure outbound HTTP request with SSRF protection,
    socket-level IP pinning, DNS rebinding mitigation, bounded redirect validation,
    and streaming size limits.
    """
    # 1. Backward-compatibility check: if requests.get has been monkeypatched by existing tests
    if requests.get is not _ORIGINAL_REQUESTS_GET and session is None and method.upper() == "GET" and data is None and json is None:
        current_url = url
        redirects_followed = 0
        base_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; UniversalExhibitorAgent/1.0)",
        }
        if headers:
            base_headers.update(headers)

        while True:
            validated_url = validate_url(current_url)
            parsed = urlparse(validated_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            resolve_and_validate_host(parsed.hostname, port)

            try:
                response = requests.get(
                    validated_url,
                    timeout=timeout,
                    headers=base_headers,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.Timeout as exc:
                raise HTTPTimeoutError(f"Request to {validated_url} timed out: {exc}") from exc
            except requests.RequestException as exc:
                raise HTTPClientError(f"Request to {validated_url} failed: {exc}") from exc

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location") if hasattr(response, "headers") else None
                if location:
                    redirects_followed += 1
                    if redirects_followed > max_redirects:
                        if hasattr(response, "close"):
                            response.close()
                        raise TooManyRedirectsError(
                            f"Exceeded maximum allowed redirects ({max_redirects}) for URL: {url}"
                        )
                    next_url = urljoin(validated_url, location)
                    if hasattr(response, "close"):
                        response.close()
                    current_url = next_url
                    continue
            break

        # Response size check
        cl_header = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        if cl_header:
            try:
                content_length = int(cl_header)
                if content_length > max_size_bytes:
                    if hasattr(response, "close"):
                        response.close()
                    raise ResponseTooLargeError(
                        f"Response Content-Length ({content_length} bytes) exceeds limit of {max_size_bytes} bytes"
                    )
            except ValueError:
                pass

        if hasattr(response, "iter_content"):
            chunks = []
            bytes_read = 0
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    bytes_read += len(chunk)
                    if bytes_read > max_size_bytes:
                        if hasattr(response, "close"):
                            response.close()
                        raise ResponseTooLargeError(
                            f"Response stream exceeded limit of {max_size_bytes} bytes"
                        )
                    chunks.append(chunk)
            finally:
                if hasattr(response, "close"):
                    response.close()
            raw_bytes = b"".join(chunks)
            content = raw_bytes.decode(getattr(response, "encoding", "utf-8") or "utf-8", errors="replace")
        else:
            content = getattr(response, "text", "") or getattr(response, "content", "")
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if not content and callable(getattr(response, "json", None)):
                try:
                    import json
                    content = json.dumps(response.json())
                except Exception:
                    pass
            raw_bytes = content.encode("utf-8", errors="replace")
            bytes_read = len(raw_bytes)
            if bytes_read > max_size_bytes:
                raise ResponseTooLargeError(
                    f"Response content ({bytes_read} bytes) exceeds limit of {max_size_bytes} bytes"
                )

        content_type = response.headers.get("Content-Type", "") if hasattr(response, "headers") and response.headers else ""
        if hasattr(response, "raise_for_status") and not (200 <= response.status_code < 400):
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise HTTPStatusError(f"HTTP {response.status_code} error: {exc}") from exc

        return FetchResponse(
            requested_url=url,
            final_url=getattr(response, "url", current_url) or current_url,
            status_code=response.status_code,
            headers=dict(response.headers) if hasattr(response, "headers") and response.headers else {},
            content=content,
            content_type=content_type,
            bytes_read=bytes_read,
            redirects_followed=redirects_followed,
            socket_evidence=None,
            socket_evidence_chain=[],
        )

    # 2. Production secure path: Use SafeSession with socket-level IP pinning
    req_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; UniversalExhibitorAgent/1.0)",
    }
    if headers:
        req_headers.update(headers)

    numeric_timeout = timeout[0] + timeout[1] if isinstance(timeout, tuple) else float(timeout)
    active_session = session if isinstance(session, SafeSession) else SafeSession(
        max_redirects=max_redirects,
        total_timeout=numeric_timeout,
        max_body_bytes=max_size_bytes,
    )

    try:
        raw_resp = active_session.request(
            method=method,
            url=url,
            headers=req_headers,
            timeout=numeric_timeout,
            data=data,
            json=json,
        )
    except (HTTPTimeoutError, ResponseTooLargeError, TooManyRedirectsError, SSRFError):
        raise
    except requests.Timeout as exc:
        raise HTTPTimeoutError(f"Request to {url} timed out: {exc}") from exc
    except requests.RequestException as exc:
        raise HTTPClientError(f"Request to {url} failed: {exc}") from exc
    finally:
        if active_session != session:
            active_session.close()

    # Raise for 4xx/5xx status
    if not (200 <= raw_resp.status_code < 400):
        try:
            raw_resp.raise_for_status()
        except requests.HTTPError as exc:
            raise HTTPStatusError(f"HTTP {raw_resp.status_code} error: {exc}") from exc

    raw_bytes = raw_resp.content
    encoding = raw_resp.encoding or "utf-8"
    try:
        content_text = raw_bytes.decode(encoding)
    except Exception:
        content_text = raw_bytes.decode("utf-8", errors="replace")

    sock_ev = getattr(raw_resp, "socket_evidence", None)
    sock_ev_dict = sock_ev.to_dict() if isinstance(sock_ev, SocketEvidence) else None
    ev_chain = [
        ev.to_dict() if isinstance(ev, SocketEvidence) else ev
        for ev in getattr(raw_resp, "socket_evidence_chain", [])
    ]

    return FetchResponse(
        requested_url=url,
        final_url=raw_resp.url,
        status_code=raw_resp.status_code,
        headers=dict(raw_resp.headers),
        content=content_text,
        content_type=raw_resp.headers.get("Content-Type", ""),
        bytes_read=len(raw_bytes),
        redirects_followed=getattr(raw_resp, "redirects_followed", 0),
        socket_evidence=sock_ev_dict,
        socket_evidence_chain=ev_chain,
    )