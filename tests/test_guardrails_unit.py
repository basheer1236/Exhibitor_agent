"""Unit test suite for URL normalization, IANA destination IP policy, and DNS guardrails."""

import ipaddress
import json
import os
import re
import socket
from unittest.mock import patch

import pytest

from app.guardrails.url import (
    ALLOWED_PORTS,
    APPROVED_BROADER_RANGES,
    APPROVED_PROJECT_EXTENSIONS,
    IANA_SPECIAL_PURPOSE_NETWORKS,
    DNSResolutionError,
    InvalidURLError,
    PolicyValidationError,
    RestrictedPortError,
    SSRFError,
    is_hostname_obviously_internal,
    is_safe_destination_ip,
    normalize_url,
    resolve_and_validate_host,
    validate_untrusted_url,
    validate_url,
)


# --------------------------------------------------
# Fixture Loading Helper
# --------------------------------------------------

def load_iana_snapshot() -> dict:
    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "iana_special_purpose_registries.json"
    )
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_unique_boundary_addresses(
    net: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Computes an ordered, deduplicated set of boundary and interior addresses."""
    candidates = [net.network_address]

    if net.version == 4:
        if net.prefixlen <= 30:
            candidates.append(net.network_address + 1)
            candidates.append(net.network_address + (net.num_addresses // 2))
            candidates.append(net.broadcast_address - 1)
            candidates.append(net.broadcast_address)
        elif net.prefixlen == 31:
            candidates.append(net.network_address + 1)
    else:
        # IPv6: No broadcast addresses (RFC 4291)
        if net.prefixlen <= 126:
            candidates.append(net.network_address + 1)
            candidates.append(net.network_address + (net.num_addresses // 2))
            candidates.append(net.network_address + net.num_addresses - 1)
        elif net.prefixlen == 127:
            candidates.append(net.network_address + 1)

    return sorted(list(set(candidates)))


# --------------------------------------------------
# 1. URL Normalization & Validation Unit Tests
# --------------------------------------------------

def test_normalize_url_basic_and_whitespace():
    raw = "   https://example.com/path?arg=1#frag   "
    assert normalize_url(raw) == "https://example.com/path?arg=1#frag"


@pytest.mark.parametrize("bad_char", ["\x00", "\x01", "\x1f", "\x7f"])
def test_normalize_url_rejects_control_characters(bad_char):
    with pytest.raises(InvalidURLError, match="control characters"):
        normalize_url(f"https://example.com/{bad_char}path")


@pytest.mark.parametrize("scheme", ["ftp", "file", "gopher", "data", "javascript"])
def test_normalize_url_rejects_unsupported_schemes(scheme):
    with pytest.raises(InvalidURLError, match="Unsupported scheme"):
        normalize_url(f"{scheme}://example.com/")


@pytest.mark.parametrize("cred_url", [
    "https://user:pass@example.com/",
    "https://admin@example.com/",
    "https://user:pass@127.0.0.1:80@example.com/",
])
def test_normalize_url_rejects_credentials(cred_url):
    with pytest.raises(InvalidURLError, match="Embedded credentials"):
        normalize_url(cred_url)


def test_normalize_url_strips_trailing_fqdn_dot():
    url = "https://example.com./exhibitors"
    normalized = normalize_url(url)
    assert normalized == "https://example.com/exhibitors"


def test_normalize_url_idna_unicode_to_punycode():
    url = "https://münchen.de/exhibitors"
    normalized = normalize_url(url)
    assert "xn--mnchen-3ya.de" in normalized


def test_normalize_url_preserves_valid_punycode():
    url = "https://xn--mnchen-3ya.de/exhibitors"
    normalized = normalize_url(url)
    assert "xn--mnchen-3ya.de" in normalized


def test_normalize_url_ipv6_zone_id_rejected():
    with pytest.raises(SSRFError, match="zone identifier"):
        normalize_url("https://[fe80::1%eth0]/")


@pytest.mark.parametrize("port", [80, 443, 8080, 8443])
def test_validate_url_allowed_ports(port):
    url = f"http://example.com:{port}/"
    res = validate_url(url)
    assert res is not None


@pytest.mark.parametrize("port", [0, 22, 25, 65536, 99999])
def test_validate_url_restricted_or_invalid_ports(port):
    url = f"http://example.com:{port}/"
    with pytest.raises((RestrictedPortError, InvalidURLError)):
        validate_url(url)


@pytest.mark.parametrize("internal_host", [
    "localhost",
    "service.local",
    "database.internal",
    "cluster.corp",
    "gateway.lan",
    "router.home",
    "resolver.arpa",
])
def test_is_hostname_obviously_internal(internal_host):
    assert is_hostname_obviously_internal(internal_host) is True


# --------------------------------------------------
# 2. IPv4-Mapped IPv6 Equivalence Tests
# --------------------------------------------------

@pytest.mark.parametrize("raw_ipv4_str", [
    # Non-unicast / Non-global (Must evaluate False in both native and mapped forms)
    "224.0.0.1",          # Multicast
    "239.255.255.250",    # Multicast SSDP
    "255.255.255.255",    # Limited Broadcast
    "0.0.0.0",            # Unspecified / This Network
    "127.0.0.1",          # Loopback
    "127.255.255.254",    # Loopback boundary
    "10.0.0.1",           # RFC 1918 Private
    "172.16.0.1",         # RFC 1918 Private
    "192.168.1.1",        # RFC 1918 Private
    "169.254.169.254",    # Link-Local / Cloud Metadata
    "100.64.0.1",         # Shared Address Space / CGNAT
    "192.0.2.1",          # Documentation (TEST-NET-1)
    "198.51.100.1",       # Documentation (TEST-NET-2)
    "203.0.113.1",        # Documentation (TEST-NET-3)
    "198.18.0.1",         # Benchmarking
    "240.0.0.1",          # Reserved
    # Globally Routable Unicast (Must evaluate True in both native and mapped forms)
    "93.184.216.34",      # Example.com
    "1.1.1.1",            # Cloudflare Public DNS
    "8.8.8.8",            # Google Public DNS
    "151.101.1.140",      # Fastly CDN
])
def test_ipv4_mapped_equivalence(raw_ipv4_str):
    native_ipv4 = ipaddress.ip_address(raw_ipv4_str)
    mapped_ipv6 = ipaddress.ip_address(f"::ffff:{raw_ipv4_str}")

    native_result = is_safe_destination_ip(native_ipv4)
    mapped_result = is_safe_destination_ip(mapped_ipv6)

    assert mapped_result == native_result, (
        f"Equivalence mismatch for {raw_ipv4_str}: "
        f"native={native_result}, mapped={mapped_result}"
    )


# --------------------------------------------------
# 3. Registry Schema & Policy Decision Validation
# --------------------------------------------------

def test_iana_registry_schema_and_policy_decisions():
    snapshot = load_iana_snapshot()
    entries = snapshot.get("entries", [])
    assert len(entries) == 50, f"Expected 50 registry rows, got {len(entries)}"

    valid_decisions = {"DENY", "UNMAP_AND_EVALUATE_IPV4"}

    for idx, entry in enumerate(entries):
        # Validate required schema fields
        for field in ("registry", "prefix", "name", "rfc", "iana_globally_reachable", "policy_decision"):
            assert field in entry, f"Missing required field '{field}' in entry {idx}"
            assert entry[field] is not None, f"Field '{field}' cannot be null in entry {idx}"

        decision = entry["policy_decision"]
        assert decision in valid_decisions, f"Unsupported policy_decision '{decision}' in entry {idx}"

        # UNMAP_AND_EVALUATE_IPV4 must be restricted exclusively to ::ffff:0:0/96
        if decision == "UNMAP_AND_EVALUATE_IPV4":
            clean_prefix = entry["prefix"].split("[")[0].strip()
            assert clean_prefix == "::ffff:0:0/96", (
                f"UNMAP_AND_EVALUATE_IPV4 assigned to invalid prefix {entry['prefix']}"
            )


# --------------------------------------------------
# 4. Dynamic Bidirectional Parity & Prefix Coverage
# --------------------------------------------------

def test_iana_registry_dynamic_bidirectional_parity():
    snapshot = load_iana_snapshot()
    entries = snapshot.get("entries", [])

    denied_networks = [ipaddress.ip_network(net) for net in IANA_SPECIAL_PURPOSE_NETWORKS]
    discrete_prefixes_found = []

    for entry in entries:
        raw_prefix_field = entry["prefix"]
        cleaned_field = re.sub(r"\[.*?\]", "", raw_prefix_field).strip()
        discrete_prefixes = [p.strip() for p in cleaned_field.split(",") if p.strip()]

        for prefix_str in discrete_prefixes:
            discrete_prefixes_found.append((prefix_str, entry))
            auth_net = ipaddress.ip_network(prefix_str)

            if entry["policy_decision"] == "DENY":
                covered = any(
                    auth_net.version == dn.version and (auth_net.subnet_of(dn) or auth_net == dn)
                    for dn in denied_networks
                )
                assert covered, f"Uncovered IANA prefix: {auth_net} ({entry['name']})"
            elif entry["policy_decision"] == "UNMAP_AND_EVALUATE_IPV4":
                assert auth_net == ipaddress.ip_network("::ffff:0:0/96")

    # Verify exact discrete prefix count (26 IPv4 + 25 IPv6 = 51)
    assert len(discrete_prefixes_found) == 51, (
        f"Expected 51 discrete prefixes, got {len(discrete_prefixes_found)}"
    )

    # Reverse parity: ensure every policy range is an exact match, approved broader range, or approved extension
    all_auth_nets = [ipaddress.ip_network(p[0]) for p in discrete_prefixes_found]
    for denied_net in denied_networks:
        net_str = str(denied_net)
        is_exact = any(denied_net == an for an in all_auth_nets if denied_net.version == an.version)
        is_broader = net_str in APPROVED_BROADER_RANGES
        is_extension = net_str in APPROVED_PROJECT_EXTENSIONS

        assert is_exact or is_broader or is_extension, (
            f"Unjustified or unexpected denial range in policy: {denied_net}"
        )


def test_unjustified_broad_range_rejected():
    """Negative test proving that wildcard or unauthorized broad ranges fail policy justification."""
    snapshot = load_iana_snapshot()
    all_auth_nets = []
    for entry in snapshot["entries"]:
        cleaned = re.sub(r"\[.*?\]", "", entry["prefix"]).strip()
        for p in cleaned.split(","):
            if p.strip():
                all_auth_nets.append(ipaddress.ip_network(p.strip()))

    invalid_ranges = ["0.0.0.0/0", "::/0", "1.0.0.0/8", "8.8.8.0/24"]
    for inv in invalid_ranges:
        net = ipaddress.ip_network(inv)
        is_exact = any(net == an for an in all_auth_nets if net.version == an.version)
        is_broader = inv in APPROVED_BROADER_RANGES
        is_extension = inv in APPROVED_PROJECT_EXTENSIONS

        assert not (is_exact or is_broader or is_extension), (
            f"Overly broad/unjustified range {inv} unexpectedly passed classification!"
        )


# --------------------------------------------------
# 5. Boundary Address Containment Tests
# --------------------------------------------------

def test_iana_registry_prefix_boundary_containment():
    snapshot = load_iana_snapshot()

    for entry in snapshot["entries"]:
        cleaned = re.sub(r"\[.*?\]", "", entry["prefix"]).strip()
        subnets = [ipaddress.ip_network(p.strip()) for p in cleaned.split(",") if p.strip()]

        for net in subnets:
            boundary_ips = get_unique_boundary_addresses(net)
            for ip in boundary_ips:
                if entry["policy_decision"] == "DENY":
                    assert is_safe_destination_ip(ip) is False, (
                        f"Leaked boundary IP {ip} in denied prefix {net} ({entry['name']})"
                    )
                elif entry["policy_decision"] == "UNMAP_AND_EVALUATE_IPV4":
                    expected = is_safe_destination_ip(ip.ipv4_mapped)
                    assert is_safe_destination_ip(ip) is expected, (
                        f"Inconsistent unmapped evaluation for {ip}"
                    )


# --------------------------------------------------
# 6. DNS Multi-Answer & Resolver Guardrail Tests
# --------------------------------------------------

def test_resolve_and_validate_host_mixed_answers_fail_closed():
    """If DNS returns a mix of public and private addresses, the entire hostname fails closed."""
    with patch("socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80)),
        ]
        with pytest.raises(SSRFError, match="unsafe IP address"):
            resolve_and_validate_host("mixed.example.com", port=80)


def test_resolve_and_validate_host_all_safe_succeeds():
    with patch("socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.35", 80)),
        ]
        ips = resolve_and_validate_host("allsafe.example.com", port=80)
        assert ips == ["93.184.216.34", "93.184.216.35"]


def test_validate_untrusted_url_pipeline():
    with patch.dict("app.guardrails.url._DNS_CACHE", {}, clear=True), patch("socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        ]
        url, ips = validate_untrusted_url("https://untrusted-exhibitor.org/exhibitors")
        assert url == "https://untrusted-exhibitor.org/exhibitors"
        assert ips == ["93.184.216.34"]
