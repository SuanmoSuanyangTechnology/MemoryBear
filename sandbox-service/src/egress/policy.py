"""Load and compile the platform-wide strict egress allowlist."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..envconf import ConfigError

_POLICY_VERSION = 1
_MAX_DOMAINS = 1024
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class EgressConfig:
    """Deployment-level strict egress switch and mounted policy path."""

    egress_enabled: bool = False
    egress_policy_path: str | None = None

    def __post_init__(self) -> None:
        if self.egress_enabled and not self.egress_policy_path:
            raise ValueError("egress_policy_path is required when egress_enabled=true")


@dataclass(frozen=True)
class StrictEgressPolicy:
    """Canonical allowlist and its complete backend network policy."""

    domains: tuple[str, ...]
    fingerprint: str

    @property
    def rule_count(self) -> int:
        return len(self.domains) * 2

    def network_config(self) -> dict[str, Any] | None:
        if not self.domains:
            return None
        rules: list[dict[str, Any]] = []
        for index, domain in enumerate(self.domains, start=1):
            rules.extend(
                (
                    {
                        "name": f"platform-http-{index}",
                        "match": {"host": domain, "scheme": "http", "port": 80},
                        "action": {"allow": True, "audit": "metadata"},
                    },
                    {
                        "name": f"platform-https-{index}",
                        "match": {"sni": domain, "scheme": "https", "port": 443},
                        "action": {"allow": True, "audit": "metadata"},
                    },
                )
            )
        return {"allowOut": [], "denyOut": [], "rules": rules}


def _canonical_domain(raw: object, index: int) -> str:
    if not isinstance(raw, str):
        raise ConfigError(f"egress policy allow_domains[{index}] must be a string")
    text = raw.strip().lower().rstrip(".")
    wildcard = text.startswith("*.")
    name = text[2:] if wildcard else text
    if not name or "*" in name:
        raise ConfigError(f"egress policy allow_domains[{index}] is not an exact or *. domain")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConfigError(f"egress policy allow_domains[{index}] is not a valid domain") from exc
    if len(name) > 253 or not all(_LABEL.fullmatch(label) for label in name.split(".")):
        raise ConfigError(f"egress policy allow_domains[{index}] is not a valid domain")
    if all(label.isdigit() for label in name.split(".")):
        raise ConfigError(f"egress policy allow_domains[{index}] must not be an IP address")
    return f"*.{name}" if wildcard else name


def _parse_document(document: object) -> tuple[str, ...]:
    if not isinstance(document, dict):
        raise ConfigError("egress policy must be a JSON object")
    expected = {"version", "allow_domains"}
    unknown = set(document) - expected
    if unknown:
        raise ConfigError(f"egress policy has unknown fields: {', '.join(sorted(unknown))}")
    if document.get("version") != _POLICY_VERSION:
        raise ConfigError(f"egress policy version must be {_POLICY_VERSION}")
    raw_domains = document.get("allow_domains")
    if not isinstance(raw_domains, list):
        raise ConfigError("egress policy allow_domains must be an array")
    if len(raw_domains) > _MAX_DOMAINS:
        raise ConfigError(f"egress policy allow_domains exceeds {_MAX_DOMAINS} entries")
    domains = tuple(_canonical_domain(value, index) for index, value in enumerate(raw_domains))
    if len(set(domains)) != len(domains):
        raise ConfigError("egress policy allow_domains contains duplicates after normalization")
    return domains


def load_strict_egress_policy(
    config: EgressConfig, *, base_dir: str | Path | None = None
) -> StrictEgressPolicy | None:
    """Load once at startup; disabled means preserving the backend's current behavior."""

    if not config.egress_enabled:
        return None
    assert config.egress_policy_path is not None
    path = Path(config.egress_policy_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read SANDBOX_EGRESS_POLICY_PATH: {exc.strerror or exc}") from exc
    try:
        document: Mapping[str, object] | object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"SANDBOX_EGRESS_POLICY_PATH is invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    domains = _parse_document(document)
    canonical = json.dumps(
        {"version": _POLICY_VERSION, "allow_domains": domains},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return StrictEgressPolicy(
        domains=domains,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
    )
