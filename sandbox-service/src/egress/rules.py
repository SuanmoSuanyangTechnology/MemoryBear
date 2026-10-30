"""Egress decision rules (design doc chapter 6), pure logic with no network access.

Evaluation order, first match wins:

1. protocol: anything that is not HTTP(S) is denied;
2. host syntax: malformed names and legacy numeric IP spellings are denied;
3. hard ban: if any address (literal or resolved) is not a public address, or lies in
   a platform-configured network (cluster CIDRs), deny. Tenants cannot lift this;
4. platform malicious-domain list: deny, tenants cannot lift this either;
5. tenant deny list: deny;
6. a domain that has not been resolved yet cannot be judged: deny (fail closed);
7. tenant allow list: allow;
8. platform default (allow for public HTTP(S) unless configured otherwise).

``deny > allow > default`` is therefore the order of steps 5, 7 and 8, and the hard
bans sit above all of them. The enforcement point resolves names itself and passes
every address it got, so DNS rebinding and mixed answers are judged on the full set.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

ACTION_ALLOW = "allow"
ACTION_DENY = "deny"

REASON_NON_HTTP = "non_http_protocol"
REASON_INVALID_HOST = "invalid_host"
REASON_INTERNAL_NETWORK = "internal_network"
REASON_MALICIOUS_DOMAIN = "malicious_domain"
REASON_TENANT_DENY = "tenant_deny"
REASON_UNRESOLVED = "unresolved"
REASON_TENANT_ALLOW = "tenant_allow"
REASON_DEFAULT_ALLOW = "default_allow"
REASON_DEFAULT_DENY = "default_deny"
REASON_AUDIT_UNAVAILABLE = "audit_unavailable"

HTTP_PROTOCOLS = frozenset({"http", "https"})

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_METADATA = ipaddress.ip_address("169.254.169.254")


@dataclass(frozen=True)
class EgressRequest:
    host: str  # domain name, IPv4 literal or IPv6 literal (brackets allowed)
    port: int
    protocol: str  # http | https | anything else the enforcement point detected
    resolved_ips: tuple[str, ...] = ()  # every address the name resolved to


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    rule: str | None = None  # the pattern that decided it, when there is one

    @property
    def allowed(self) -> bool:
        return self.action == ACTION_ALLOW


def _deny(reason: str, rule: str | None = None) -> Decision:
    return Decision(ACTION_DENY, reason, rule)


def _allow(reason: str, rule: str | None = None) -> Decision:
    return Decision(ACTION_ALLOW, reason, rule)


def _unwrap(ip: IPAddress) -> IPAddress:
    """Judge the IPv4 address hidden inside mapped / 6to4 / NAT64 IPv6 spellings."""

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def parse_ip(text: str) -> IPAddress | None:
    host = text.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = host.split("%", 1)[0]  # drop an IPv6 zone id
    try:
        return _unwrap(ipaddress.ip_address(host))
    except ValueError:
        return None


def normalize_domain(host: str) -> str | None:
    """Lower-case ASCII form of a domain name, or None when it is not a valid name."""

    name = host.strip().lower().rstrip(".")
    if not name:
        return None
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(name) > 253:
        return None
    labels = name.split(".")
    if not all(_LABEL.match(label) for label in labels):
        return None
    if all(_NUMERIC_LABEL.match(label) for label in labels):
        return None  # "2130706433", "0x7f.1", "0177.0.0.1": ambiguous IPv4 spellings
    return name


@dataclass(frozen=True)
class Pattern:
    """One tenant list entry: exact domain, ``*.`` wildcard (subdomains only) or CIDR."""

    raw: str
    kind: str  # domain | wildcard | cidr
    domain: str = ""
    network: IPNetwork | None = field(default=None, compare=False)

    def matches_host(self, host: str) -> bool:
        if self.kind == "domain":
            return host == self.domain
        if self.kind == "wildcard":
            return host.endswith("." + self.domain)
        return False

    def matches_ip(self, ip: IPAddress) -> bool:
        return self.network is not None and ip in self.network


def parse_pattern(raw: str) -> Pattern:
    text = raw.strip().lower()
    if not text:
        raise ValueError("empty egress pattern")
    ip = parse_ip(text) if "/" not in text else None
    if "/" in text or ip is not None:
        try:
            return Pattern(raw, "cidr", network=ipaddress.ip_network(text, strict=True))
        except ValueError as exc:
            raise ValueError(f"invalid CIDR in egress pattern: {raw!r}") from exc
    if text.startswith("*."):
        name = normalize_domain(text[2:])
        if name is None:
            raise ValueError(f"invalid wildcard domain in egress pattern: {raw!r}")
        return Pattern(raw, "wildcard", domain=name)
    name = normalize_domain(text)
    if name is None:
        raise ValueError(f"invalid domain in egress pattern: {raw!r}")
    return Pattern(raw, "domain", domain=name)


@dataclass(frozen=True)
class TenantRules:
    allow: tuple[Pattern, ...] = ()
    deny: tuple[Pattern, ...] = ()

    @classmethod
    def from_lists(cls, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> TenantRules:
        return cls(
            allow=tuple(parse_pattern(p) for p in allow),
            deny=tuple(parse_pattern(p) for p in deny),
        )


class EgressPolicy:
    """Platform-wide egress policy; tenant lists are passed per evaluation."""

    def __init__(
        self,
        *,
        blocked_cidrs: Iterable[str] = (),
        malicious_domains: Iterable[str] = (),
        default_action: str = ACTION_ALLOW,
    ) -> None:
        if default_action not in (ACTION_ALLOW, ACTION_DENY):
            raise ValueError("default_action must be 'allow' or 'deny'")
        self._blocked = tuple(ipaddress.ip_network(c, strict=True) for c in blocked_cidrs)
        self._malicious = tuple(parse_pattern(d) for d in malicious_domains)
        if any(p.kind == "cidr" for p in self._malicious):
            raise ValueError("malicious_domains takes domain names only")
        self._default = default_action

    def _is_blocked_ip(self, ip: IPAddress) -> bool:
        return ip == _METADATA or not ip.is_global or any(ip in n for n in self._blocked)

    def evaluate(self, req: EgressRequest, tenant: TenantRules | None = None) -> Decision:
        tenant = tenant or TenantRules()

        if req.protocol.lower() not in HTTP_PROTOCOLS:
            return _deny(REASON_NON_HTTP)
        if not 1 <= req.port <= 65535:
            return _deny(REASON_INVALID_HOST, "port")

        host_ip = parse_ip(req.host)
        host: str | None = None
        ips: list[IPAddress] = []
        if host_ip is not None:
            ips.append(host_ip)
        else:
            host = normalize_domain(req.host)
            if host is None:
                return _deny(REASON_INVALID_HOST)
        for text in req.resolved_ips:
            ip = parse_ip(text)
            if ip is None:
                return _deny(REASON_INVALID_HOST, "resolved_ips")
            ips.append(ip)

        for ip in ips:
            if self._is_blocked_ip(ip):
                return _deny(REASON_INTERNAL_NETWORK, str(ip))

        if host is not None:
            for pat in self._malicious:
                if pat.matches_host(host):
                    return _deny(REASON_MALICIOUS_DOMAIN, pat.raw)

        for pat in tenant.deny:
            if (host is not None and pat.matches_host(host)) or any(
                pat.matches_ip(ip) for ip in ips
            ):
                return _deny(REASON_TENANT_DENY, pat.raw)

        if host is not None and not ips:
            return _deny(REASON_UNRESOLVED)

        for pat in tenant.allow:
            if host is not None and pat.matches_host(host):
                return _allow(REASON_TENANT_ALLOW, pat.raw)
            if pat.kind == "cidr" and ips and all(pat.matches_ip(ip) for ip in ips):
                return _allow(REASON_TENANT_ALLOW, pat.raw)

        if self._default == ACTION_ALLOW:
            return _allow(REASON_DEFAULT_ALLOW)
        return _deny(REASON_DEFAULT_DENY)
