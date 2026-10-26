from __future__ import annotations

import json

import pytest

from src.egress.policy import EgressConfig, StrictEgressPolicy, load_strict_egress_policy
from src.envconf import ConfigError


def write_policy(tmp_path, document: object):
    path = tmp_path / "egress.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_disabled_policy_needs_no_file():
    assert load_strict_egress_policy(EgressConfig()) is None


def test_enabled_policy_requires_a_path():
    with pytest.raises(ValueError, match="egress_policy_path"):
        EgressConfig(egress_enabled=True)


def test_load_normalizes_domains_and_compiles_standard_http_https_rules(tmp_path):
    path = write_policy(
        tmp_path,
        {"version": 1, "allow_domains": ["PyPI.ORG.", "*.PythonHosted.org"]},
    )

    policy = load_strict_egress_policy(
        EgressConfig(True, path.name), base_dir=tmp_path
    )

    assert policy is not None
    assert policy.domains == ("pypi.org", "*.pythonhosted.org")
    assert len(policy.fingerprint) == 16
    assert policy.rule_count == 4
    assert policy.network_config() == {
        "allowOut": [],
        "denyOut": [],
        "rules": [
            {
                "name": "platform-http-1",
                "match": {"host": "pypi.org", "scheme": "http", "port": 80},
                "action": {"allow": True, "audit": "metadata"},
            },
            {
                "name": "platform-https-1",
                "match": {"sni": "pypi.org", "scheme": "https", "port": 443},
                "action": {"allow": True, "audit": "metadata"},
            },
            {
                "name": "platform-http-2",
                "match": {"host": "*.pythonhosted.org", "scheme": "http", "port": 80},
                "action": {"allow": True, "audit": "metadata"},
            },
            {
                "name": "platform-https-2",
                "match": {"sni": "*.pythonhosted.org", "scheme": "https", "port": 443},
                "action": {"allow": True, "audit": "metadata"},
            },
        ],
    }


def test_empty_allowlist_compiles_to_deny_all_without_l7_rules(tmp_path):
    path = write_policy(tmp_path, {"version": 1, "allow_domains": []})
    policy = load_strict_egress_policy(EgressConfig(True, str(path)))
    assert policy is not None
    assert policy.network_config() is None


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"version": 2, "allow_domains": []},
        {"version": 1},
        {"version": 1, "allow_domains": "pypi.org"},
        {"version": 1, "allow_domains": ["1.2.3.4"]},
        {"version": 1, "allow_domains": ["foo.*.example.com"]},
        {"version": 1, "allow_domains": ["bad_domain.example"]},
        {"version": 1, "allow_domains": ["example.com", "EXAMPLE.COM."]},
        {"version": 1, "allow_domains": [], "deny_domains": []},
    ],
)
def test_invalid_documents_fail_closed_at_load(tmp_path, document):
    path = write_policy(tmp_path, document)
    with pytest.raises(ConfigError):
        load_strict_egress_policy(EgressConfig(True, str(path)))


def test_missing_or_malformed_file_names_the_config(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(ConfigError, match="SANDBOX_EGRESS_POLICY_PATH"):
        load_strict_egress_policy(EgressConfig(True, str(missing)))

    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="SANDBOX_EGRESS_POLICY_PATH"):
        load_strict_egress_policy(EgressConfig(True, str(malformed)))


def test_policy_object_is_deterministic():
    first = StrictEgressPolicy(("example.com",), "same")
    second = StrictEgressPolicy(("example.com",), "same")
    assert first.network_config() == second.network_config()
