from __future__ import annotations

import pytest

from src.clock import ManualClock
from src.egress import (
    EgressContext,
    EgressGate,
    EgressPolicy,
    EgressRequest,
    InMemoryAuditSink,
    TenantRules,
    parse_pattern,
)
from src.egress.rules import (
    REASON_AUDIT_UNAVAILABLE,
    REASON_DEFAULT_ALLOW,
    REASON_DEFAULT_DENY,
    REASON_INTERNAL_NETWORK,
    REASON_INVALID_HOST,
    REASON_MALICIOUS_DOMAIN,
    REASON_NON_HTTP,
    REASON_TENANT_ALLOW,
    REASON_TENANT_DENY,
    REASON_UNRESOLVED,
)

PUBLIC = "93.184.216.34"


def req(host="example.com", ips=(PUBLIC,), protocol="https", port=443) -> EgressRequest:
    return EgressRequest(host, port, protocol, tuple(ips))


@pytest.fixture
def policy() -> EgressPolicy:
    return EgressPolicy(blocked_cidrs=["198.51.100.0/24"], malicious_domains=["*.evil.test"])


# -- defaults and protocol ------------------------------------------------------


def test_public_https_is_allowed_by_default(policy):
    d = policy.evaluate(req())
    assert d.allowed and d.reason == REASON_DEFAULT_ALLOW


@pytest.mark.parametrize("protocol", ["tcp", "udp", "dns", "ssh", ""])
def test_non_http_is_denied(policy, protocol):
    d = policy.evaluate(req(protocol=protocol))
    assert not d.allowed and d.reason == REASON_NON_HTTP


def test_non_http_cannot_be_opened_by_a_tenant(policy):
    tenant = TenantRules.from_lists(allow=["example.com"])
    assert not policy.evaluate(req(protocol="tcp"), tenant).allowed


def test_default_deny_policy(policy):
    strict = EgressPolicy(default_action="deny")
    d = strict.evaluate(req())
    assert not d.allowed and d.reason == REASON_DEFAULT_DENY
    tenant = TenantRules.from_lists(allow=["example.com"])
    assert strict.evaluate(req(), tenant).reason == REASON_TENANT_ALLOW


def test_bad_port_is_denied(policy):
    assert policy.evaluate(req(port=0)).reason == REASON_INVALID_HOST
    assert policy.evaluate(req(port=70000)).reason == REASON_INVALID_HOST


# -- hard bans ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "169.254.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "198.51.100.7",  # platform-configured cluster network
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:10.0.0.1",  # IPv4-mapped
        "2002:0a00:0001::1",  # 6to4 wrapping 10.0.0.1
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
    ],
)
def test_internal_ip_literals_are_denied(policy, ip):
    host = f"[{ip}]" if ":" in ip else ip
    d = policy.evaluate(req(host=host, ips=()))
    assert not d.allowed and d.reason == REASON_INTERNAL_NETWORK


def test_domain_resolving_to_internal_ip_is_denied(policy):
    d = policy.evaluate(req(ips=("10.1.2.3",)))
    assert d.reason == REASON_INTERNAL_NETWORK


def test_mixed_resolution_is_denied_if_any_address_is_internal(policy):
    d = policy.evaluate(req(ips=(PUBLIC, "192.168.0.9")))
    assert d.reason == REASON_INTERNAL_NETWORK and d.rule == "192.168.0.9"


def test_tenant_allow_cannot_open_internal_networks(policy):
    tenant = TenantRules.from_lists(allow=["10.0.0.0/8", "example.com", "169.254.169.254"])
    assert policy.evaluate(req(host="10.0.0.5", ips=()), tenant).reason == REASON_INTERNAL_NETWORK
    assert policy.evaluate(req(ips=("10.0.0.5",)), tenant).reason == REASON_INTERNAL_NETWORK
    assert policy.evaluate(req(host="169.254.169.254", ips=()), tenant).reason == (
        REASON_INTERNAL_NETWORK
    )


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "0177.0.0.1", "127.1", "1.2.3"])
def test_legacy_numeric_ip_spellings_are_rejected(policy, host):
    d = policy.evaluate(req(host=host, ips=(PUBLIC,)))
    assert not d.allowed and d.reason == REASON_INVALID_HOST


@pytest.mark.parametrize("host", ["", " ", "a b.com", "exa$mple.com", "-bad.com", "a..b.com"])
def test_malformed_hosts_are_rejected(policy, host):
    assert policy.evaluate(req(host=host)).reason == REASON_INVALID_HOST


def test_unparseable_resolved_ip_is_rejected(policy):
    assert policy.evaluate(req(ips=("not-an-ip",))).reason == REASON_INVALID_HOST


def test_unresolved_domain_fails_closed(policy):
    d = policy.evaluate(req(ips=()))
    assert not d.allowed and d.reason == REASON_UNRESOLVED


def test_public_ip_literal_is_allowed(policy):
    assert policy.evaluate(req(host=PUBLIC, ips=())).allowed


# -- malicious domains, deny > allow > default ---------------------------------------


def test_malicious_domain_denied_and_not_overridable(policy):
    tenant = TenantRules.from_lists(allow=["*.evil.test"])
    d = policy.evaluate(req(host="x.evil.test"), tenant)
    assert d.reason == REASON_MALICIOUS_DOMAIN and d.rule == "*.evil.test"


def test_tenant_deny_beats_tenant_allow(policy):
    tenant = TenantRules.from_lists(allow=["*.example.com"], deny=["api.example.com"])
    d = policy.evaluate(req(host="api.example.com"), tenant)
    assert d.reason == REASON_TENANT_DENY and d.rule == "api.example.com"
    assert policy.evaluate(req(host="www.example.com"), tenant).reason == REASON_TENANT_ALLOW


def test_tenant_allow_beats_default_deny():
    strict = EgressPolicy(default_action="deny")
    tenant = TenantRules.from_lists(allow=["pypi.org"])
    assert strict.evaluate(req(host="pypi.org"), tenant).allowed
    assert not strict.evaluate(req(host="other.org"), tenant).allowed


def test_tenant_deny_beats_default_allow(policy):
    tenant = TenantRules.from_lists(deny=["blocked.com"])
    assert not policy.evaluate(req(host="blocked.com"), tenant).allowed
    assert policy.evaluate(req(host="fine.com"), tenant).allowed


def test_wildcard_matches_subdomains_only_and_respects_label_boundary(policy):
    tenant = TenantRules.from_lists(deny=["*.example.com"])
    assert not policy.evaluate(req(host="a.b.example.com"), tenant).allowed
    assert policy.evaluate(req(host="example.com"), tenant).allowed
    assert policy.evaluate(req(host="badexample.com"), tenant).allowed


def test_host_matching_is_case_and_trailing_dot_insensitive(policy):
    tenant = TenantRules.from_lists(deny=["Example.COM"])
    assert not policy.evaluate(req(host="EXAMPLE.com."), tenant).allowed


def test_tenant_cidr_deny_matches_any_resolved_address(policy):
    tenant = TenantRules.from_lists(deny=["93.184.216.0/24"])
    d = policy.evaluate(req(), tenant)
    assert d.reason == REASON_TENANT_DENY and d.rule == "93.184.216.0/24"


def test_tenant_cidr_allow_needs_every_address_inside():
    strict = EgressPolicy(default_action="deny")
    tenant = TenantRules.from_lists(allow=["93.184.216.0/24"])
    assert strict.evaluate(req(ips=("93.184.216.1", "93.184.216.2")), tenant).allowed
    assert not strict.evaluate(req(ips=("93.184.216.1", "8.8.8.8")), tenant).allowed


# -- pattern parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["", "  ", "*.", "*.*.com", "bad host", "10.0.0.1/33", "10.0.0.1/8"]
)
def test_invalid_patterns_raise(raw):
    with pytest.raises(ValueError):
        parse_pattern(raw)


def test_single_ip_pattern_is_a_host_route():
    p = parse_pattern("203.0.113.9")
    assert p.kind == "cidr" and str(p.network) == "203.0.113.9/32"


def test_policy_rejects_cidr_in_malicious_domains():
    with pytest.raises(ValueError):
        EgressPolicy(malicious_domains=["1.2.3.0/24"])


def test_policy_rejects_unknown_default_action():
    with pytest.raises(ValueError):
        EgressPolicy(default_action="maybe")


# -- gate and audit -----------------------------------------------------------------


CTX = EgressContext("t1", "w1", "u1", "sbx-1")


def test_gate_records_every_decision_with_identifiers(policy):
    sink = InMemoryAuditSink()
    clock = ManualClock()
    gate = EgressGate(policy, sink, clock)
    gate.decide(CTX, req())
    gate.decide(CTX, req(host="10.0.0.1", ips=()))
    first, second = sink.events
    assert first.decision.allowed and not second.decision.allowed
    row = second.to_dict()
    assert row["ts_ms"] == clock.now_ms()
    assert (row["tenant_id"], row["workspace_id"], row["end_user_id"], row["sandbox_id"]) == (
        "t1",
        "w1",
        "u1",
        "sbx-1",
    )
    assert row["action"] == "deny" and row["reason"] == REASON_INTERNAL_NETWORK


def test_gate_fails_closed_when_the_sink_breaks(policy):
    class Broken:
        def record(self, event):
            raise OSError("disk full")

    d = EgressGate(policy, Broken()).decide(CTX, req())
    assert not d.allowed and d.reason == REASON_AUDIT_UNAVAILABLE


@pytest.mark.parametrize("missing", ["tenant_id", "workspace_id", "end_user_id", "sandbox_id"])
def test_context_requires_all_identifiers(missing):
    ids = {"tenant_id": "t", "workspace_id": "w", "end_user_id": "u", "sandbox_id": "s"}
    ids[missing] = ""
    with pytest.raises(ValueError):
        EgressContext(**ids)


def test_in_memory_sink_is_bounded(policy):
    sink = InMemoryAuditSink(maxlen=2)
    gate = EgressGate(policy, sink)
    for host in ("a.com", "b.com", "c.com"):
        gate.decide(CTX, req(host=host))
    assert [e.request.host for e in sink.events] == ["b.com", "c.com"]
