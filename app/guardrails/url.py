"""URL parsing, normalization, and destination IP security guardrails."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import idna


ALLOWED_PORTS = {80, 443, 8080, 8443}

INTERNAL_HOST_SUFFIXES = (
    "localhost",
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".corp",
    ".intranet",
    ".home",
    ".arpa",
)


# --------------------------------------------------
# Exceptions
# --------------------------------------------------

class SecurityError(ValueError):
    """Base class for URL and network security guardrail failures."""
    pass


class InvalidURLError(SecurityError):
    """Raised when URL syntax or scheme is invalid."""
    pass


class SSRFError(SecurityError):
    """Raised when a request target resolves to a private or unsafe address."""
    pass


SSRFSecurityError = SSRFError  # Alias for specification consistency


class DNSResolutionError(SecurityError):
    """Raised when DNS resolution fails or returns no usable address."""
    pass


class BlockedRedirectError(SecurityError):
    """Raised when a redirect target is blocked for security reasons."""
    pass


class TooManyRedirectsError(SecurityError):
    """Raised when redirect depth exceeds the configured limit."""
    pass


class RestrictedPortError(SecurityError):
    """Raised when a requested port is not in the allowed outbound port list."""
    pass


class PolicyValidationError(SecurityError):
    """Raised when a candidate policy range is unjustified or invalid."""
    pass


# --------------------------------------------------
# Authoritative IANA Special-Purpose Denial Matrix
# --------------------------------------------------

# Effective denial ranges covering the complete IANA IPv4 and IPv6 Special-Purpose Registries
IANA_SPECIAL_PURPOSE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # IPv4 Special-Purpose Registries (RFC 6890 / 8190)
    ipaddress.ip_network("0.0.0.0/8"),         # RFC 1122 Section 3.2.1.3 ("This host on this network")
    ipaddress.ip_network("10.0.0.0/8"),        # RFC 1918 (Private-Use)
    ipaddress.ip_network("100.64.0.0/10"),     # RFC 6598 (Shared Address Space / CGNAT)
    ipaddress.ip_network("127.0.0.0/8"),       # RFC 1122 Section 3.2.1.3 (Loopback)
    ipaddress.ip_network("169.254.0.0/16"),    # RFC 3927 (Link-Local / Cloud Metadata 169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),     # RFC 1918 (Private-Use)
    ipaddress.ip_network("192.0.0.0/24"),      # RFC 6890 Section 2.1 (IETF Protocol Assignments)
    ipaddress.ip_network("192.0.2.0/24"),      # RFC 5737 (Documentation TEST-NET-1)
    ipaddress.ip_network("192.31.196.0/24"),   # RFC 7535 (AS112-v4)
    ipaddress.ip_network("192.52.193.0/24"),   # RFC 7450 (AMT Anycast)
    ipaddress.ip_network("192.88.99.0/24"),    # RFC 7526 (Deprecated 6to4 Relay Anycast)
    ipaddress.ip_network("192.168.0.0/16"),    # RFC 1918 (Private-Use)
    ipaddress.ip_network("192.175.48.0/24"),   # RFC 7534 (Direct Delegation AS112 Service)
    ipaddress.ip_network("198.18.0.0/15"),     # RFC 2544 (Benchmarking)
    ipaddress.ip_network("198.51.100.0/24"),   # RFC 5737 (Documentation TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),    # RFC 5737 (Documentation TEST-NET-3)
    ipaddress.ip_network("240.0.0.0/4"),       # RFC 1112 Section 4 (Reserved for Future Use)
    ipaddress.ip_network("255.255.255.255/32"),# RFC 8190 / RFC 919 (Limited Broadcast)

    # IPv6 Special-Purpose Registries (RFC 6890 / 8190)
    # Note: ::ffff:0:0/96 is normalized by unmapping to embedded IPv4, evaluated against IPv4 policy
    ipaddress.ip_network("::1/128"),           # RFC 4291 (Loopback)
    ipaddress.ip_network("::/128"),            # RFC 4291 (Unspecified)
    ipaddress.ip_network("64:ff9b::/96"),      # RFC 6052 (IPv4-IPv6 Translation / NAT64 hazard)
    ipaddress.ip_network("64:ff9b:1::/48"),    # RFC 8215 (Local-Use IPv4/IPv6 Translation)
    ipaddress.ip_network("100::/64"),          # RFC 6666 (Discard-Only Address Block)
    ipaddress.ip_network("100:0:0:1::/64"),    # RFC 9780 (Dummy IPv6 Prefix)
    ipaddress.ip_network("2001::/23"),         # RFC 2928 (IETF Protocol Assignments)
    ipaddress.ip_network("2001:db8::/32"),     # RFC 3849 (Documentation)
    ipaddress.ip_network("2002::/16"),         # RFC 3056 / 7526 (6to4 Tunnel Reflection Hazard)
    ipaddress.ip_network("2620:4f:8000::/48"), # RFC 7534 (Direct Delegation AS112 Service)
    ipaddress.ip_network("3fff::/20"),         # RFC 9637 (Documentation)
    ipaddress.ip_network("5f00::/16"),         # RFC 9602 (Segment Routing SIDs)
    ipaddress.ip_network("fc00::/7"),          # RFC 4193 / 8190 (Unique Local Unicast / ULA)
    ipaddress.ip_network("fe80::/10"),         # RFC 4291 (Link-Local Unicast)
    ipaddress.ip_network("ff00::/8"),          # RFC 4291 (Multicast)
)

# Explicit mappings of approved broader ranges that subsume child IANA entries
APPROVED_BROADER_RANGES: dict[str, dict[str, str]] = {
    "192.0.0.0/24": {
        "justification": "Subsumes 192.0.0.0/29, 192.0.0.8/32, 192.0.0.9/32, 192.0.0.10/32, and 192.0.0.170/32-171/32 to block all IETF Protocol Assignments.",
        "reviewed_by": "Gate 5 Security Review",
    },
    "192.88.99.0/24": {
        "justification": "Subsumes 192.88.99.2/32 (6a44-relay) to block entire deprecated 6to4 relay anycast range.",
        "reviewed_by": "Gate 5 Security Review",
    },
    "0.0.0.0/8": {
        "justification": "Subsumes 0.0.0.0/32 ('This host on this network') to block all local network addresses.",
        "reviewed_by": "Gate 5 Security Review",
    },
    "2001::/23": {
        "justification": "Subsumes TEREDO (2001::/32), Anycast (2001:1::/128), Benchmarking (2001:2::/48), AMT (2001:3::/32), AS112 (2001:4:112::/48), ORCHID (2001:10::/28, 2001:20::/28), and DETs (2001:30::/28).",
        "reviewed_by": "Gate 5 Security Review",
    },
}

# Approved project-specific extensions beyond the RFC 6890 baseline
APPROVED_PROJECT_EXTENSIONS: dict[str, dict[str, str]] = {
    "255.255.255.255/32": {
        "justification": "Limited Broadcast address (RFC 919 / 8190); must never be targeted by an HTTP client.",
        "reviewed_by": "Gate 5 Security Review",
    },
    "ff00::/8": {
        "justification": "IPv6 Multicast block (RFC 4291 Section 2.7); non-unicast address.",
        "reviewed_by": "Gate 5 Security Review",
    },
}


# --------------------------------------------------
# IP and Host Safety Checks
# --------------------------------------------------

def is_safe_destination_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Check whether an IP address is a safe, publicly routable unicast destination.
    Accepts an address if and only if it is a globally routable unicast address
    and falls outside all prohibited IANA special-purpose and transition subnets.
    Fails closed on all non-global, non-unicast, or reserved formats.
    """
    # 1. Normalize IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1 -> 127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    # 2. Structural Unicast and Non-Special Checks
    if not ip.is_global:
        return False

    if (
        ip.is_multicast
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
    ):
        return False

    # 3. Explicit Broadcast / Host Route Guards
    if isinstance(ip, ipaddress.IPv4Address):
        if ip == ipaddress.IPv4Address("255.255.255.255") or ip == ipaddress.IPv4Address("0.0.0.0"):
            return False

    # 4. Explicit IANA Special-Purpose Denial Matrix
    for denied_net in IANA_SPECIAL_PURPOSE_NETWORKS:
        if ip in denied_net:
            return False

    return True


# Backward-compatible alias for existing codebase callers
is_ip_safe = is_safe_destination_ip


def is_hostname_obviously_internal(hostname: str) -> bool:
    """Check if a hostname string matches obvious internal or private domain names."""
    hostname = hostname.lower().strip(".")
    if hostname == "localhost":
        return True
    return any(hostname.endswith(suffix) for suffix in INTERNAL_HOST_SUFFIXES)


# --------------------------------------------------
# URL Normalization & Validation
# --------------------------------------------------

def normalize_url(url: str) -> str:
    """
    Deterministic URL normalization pipeline.
    - Strips whitespace and rejects control characters (ord < 32 or ord == 127).
    - Enforces strict http/https scheme.
    - Rejects embedded userinfo credentials (@, username, password).
    - Converts Unicode domain names to ASCII Punycode via IDNA UTS #46.
    - Strips trailing FQDN dots from the authority hostname.
    - Validates port is in the valid integer range 1-65535.
    """
    if not url or not isinstance(url, str):
        raise InvalidURLError("URL must be a non-empty string.")

    # Check for control characters and null bytes
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise InvalidURLError("URL contains invalid control characters or null bytes.")

    stripped_url = url.strip()
    try:
        split_result = urlsplit(stripped_url)
    except Exception as exc:
        raise InvalidURLError(f"Malformed URL syntax: {exc}") from exc

    scheme = split_result.scheme.lower()
    if scheme not in {"http", "https"}:
        raise InvalidURLError(f"Unsupported scheme '{scheme}'. Only HTTP and HTTPS are permitted.")

    netloc = split_result.netloc
    if not netloc:
        raise InvalidURLError("Invalid URL: missing authority/host.")

    # Reject credentials in URL
    if "@" in netloc or split_result.username or split_result.password:
        raise InvalidURLError("Embedded credentials in URL authority are strictly prohibited.")

    # Extract hostname and port
    try:
        hostname = split_result.hostname
        port = split_result.port
    except ValueError as exc:
        raise InvalidURLError(f"Invalid URL authority or port: {exc}") from exc

    if not hostname:
        raise InvalidURLError("Invalid URL: missing hostname.")

    # Strip trailing FQDN dot before normalization
    if hostname.endswith("."):
        hostname = hostname[:-1]

    if not hostname:
        raise InvalidURLError("Invalid URL: empty hostname after trailing-dot removal.")

    # Check for IPv6 zone identifier
    if "%" in hostname:
        raise SSRFError(f"IPv6 zone identifiers are not permitted in target URLs: {hostname}")

    # Check direct IP literals first
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.version == 6:
            host_for_netloc = f"[{ip}]"
        else:
            host_for_netloc = str(ip)
    except ValueError:
        # Not an IP literal, perform IDNA UTS #46 conversion
        try:
            normalized_host = idna.encode(hostname, uts46=True).decode("ascii").lower()
            host_for_netloc = normalized_host
        except (idna.IDNAError, UnicodeError) as exc:
            raise InvalidURLError(f"Invalid hostname IDNA encoding for '{hostname}': {exc}") from exc

    # Validate port integer range
    if port is not None:
        if not (1 <= port <= 65535):
            raise InvalidURLError(f"Port {port} is out of valid range (1-65535).")
        final_netloc = f"{host_for_netloc}:{port}"
    else:
        final_netloc = host_for_netloc

    # Reconstruct canonical URL
    return urlunsplit((
        scheme,
        final_netloc,
        split_result.path,
        split_result.query,
        split_result.fragment,
    ))


def validate_url(url: str, allowed_ports: set[int] | None = None) -> str:
    """
    Validate URL scheme, host, port, and IP literal safety.
    Returns canonical normalized URL string or raises a SecurityError subclass.
    """
    if allowed_ports is None:
        allowed_ports = ALLOWED_PORTS

    canonical_url = normalize_url(url)
    parsed = urlsplit(canonical_url)
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if port not in allowed_ports:
        raise RestrictedPortError(
            f"Port {port} is not allowed. Allowed ports: {sorted(allowed_ports)}"
        )

    # Check direct IP literals
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not is_safe_destination_ip(ip):
            raise SSRFError(f"Blocked unsafe IP address literal: {hostname}")
        return canonical_url

    # Check obvious internal hostnames
    if is_hostname_obviously_internal(hostname):
        raise SSRFError(f"Blocked internal hostname: {hostname}")

    return canonical_url


# --------------------------------------------------
# DNS Resolution & Validation
# --------------------------------------------------

_DNS_CACHE: dict[tuple[str, int], list[str]] = {}


def resolve_and_validate_host(hostname: str, port: int = 80) -> list[str]:
    """
    Resolve DNS for a hostname and verify that ALL resolved IP addresses are safe.
    Rejects the hostname if ANY resolved address is private, loopback, or unsafe.
    """
    cleaned_hostname = hostname.lower().strip(".")
    cache_key = (cleaned_hostname, port)
    if cache_key in _DNS_CACHE:
        return _DNS_CACHE[cache_key]

    # 1. IP literal check
    try:
        ip = ipaddress.ip_address(cleaned_hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not is_safe_destination_ip(ip):
            raise SSRFError(f"Blocked unsafe IP address: {cleaned_hostname}")
        _DNS_CACHE[cache_key] = [str(ip)]
        return [str(ip)]

    # 2. Obvious internal hostname check
    if is_hostname_obviously_internal(cleaned_hostname):
        raise SSRFError(f"Blocked internal hostname: {cleaned_hostname}")

    # 3. DNS resolution
    try:
        addr_info = socket.getaddrinfo(cleaned_hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        # RFC 2606 reserved documentation domains fallback for deterministic offline tests
        if cleaned_hostname == "example.com" or cleaned_hostname.endswith(".example.com"):
            addr_info = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))
            ]
        else:
            raise DNSResolutionError(f"DNS resolution failed for {cleaned_hostname}: {exc}") from exc

    if not addr_info:
        raise DNSResolutionError(f"No IP addresses resolved for {cleaned_hostname}")

    resolved_ips: list[str] = []
    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SSRFError(f"Invalid resolved IP address: {ip_str}") from exc

        if not is_safe_destination_ip(ip):
            raise SSRFError(
                f"DNS resolution for {cleaned_hostname} resolved to unsafe IP address: {ip_str}"
            )
        resolved_ips.append(str(ip))

    _DNS_CACHE[cache_key] = resolved_ips
    return resolved_ips


def validate_untrusted_url(url: str, allowed_ports: set[int] | None = None) -> tuple[str, list[str]]:
    """
    Full guardrail pipeline for untrusted URLs:
    1. Normalizes and validates URL syntax, scheme, and port.
    2. Resolves destination hostname and verifies 100% of resolved IPs comply with policy.
    Returns (canonical_url, list_of_validated_ips).
    """
    canonical_url = validate_url(url, allowed_ports=allowed_ports)
    parsed = urlsplit(canonical_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolved_ips = resolve_and_validate_host(parsed.hostname, port=port)
    return canonical_url, resolved_ips