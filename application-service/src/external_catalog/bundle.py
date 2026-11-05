"""Bundle shell assembly: canonical hash, credential redaction, shell checks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..errors import BizCode
from .errors import ExternalCatalogError
from .types import VERSION_PATTERN, is_supported_type

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "client_secret",
        "authorization",
        "credential",
        "credentials",
    }
)

SENSITIVE_CONTAINER_KEYS = frozenset({"connection_config", "auth_config"})
# Credential containers keep these non-secret descriptors; every other value
# is dropped so the installer knows which fields to refill.
CONNECTION_CONFIG_DESCRIPTORS = frozenset({"auth_type", "timeout"})
AUTH_CONFIG_DESCRIPTORS = frozenset({"auth_type"})

MAX_MANIFEST_BYTES = 5 * 1024 * 1024


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def compute_content_hash(manifest: dict[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key != "content_hash"}
    return f"sha256:{hashlib.sha256(canonical_json(body)).hexdigest()}"


def finalize_bundle(manifest: dict[str, Any]) -> dict[str, Any]:
    bundle = {key: value for key, value in manifest.items() if key != "content_hash"}
    bundle["content_hash"] = compute_content_hash(bundle)
    if len(canonical_json(bundle)) > MAX_MANIFEST_BYTES:
        raise ExternalCatalogError(
            BizCode.EXTERNAL_CATALOG_INVALID,
            "bundle 清单超过 5 MB 上限",
            data={"reason": "BUNDLE_TOO_LARGE"},
        )
    return bundle


def _blank_credentials(
    value: Any, path: str, descriptors: frozenset[str]
) -> tuple[Any, list[str]]:
    """Keep field names, replace every credential value with ``None``."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        paths: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() in descriptors:
                cleaned[key] = item
            elif isinstance(item, dict):
                child, child_paths = _blank_credentials(item, child_path, frozenset())
                cleaned[key] = child
                paths.extend(child_paths)
            elif isinstance(item, list):
                cleaned[key] = []
                paths.append(child_path)
            else:
                cleaned[key] = None
                paths.append(child_path)
        return cleaned, paths
    if isinstance(value, list):
        return [], [path]
    return None, [path]


def redact_secrets(value: Any, path: str = "") -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        paths: list[str] = []
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if lowered in SENSITIVE_KEYS:
                paths.append(child_path)
                continue
            if lowered in SENSITIVE_CONTAINER_KEYS and item:
                descriptors = (
                    CONNECTION_CONFIG_DESCRIPTORS
                    if lowered == "connection_config"
                    else AUTH_CONFIG_DESCRIPTORS
                )
                if isinstance(item, dict):
                    child, child_paths = _blank_credentials(
                        item, child_path, descriptors
                    )
                    cleaned[key] = child
                    paths.extend(child_paths)
                else:
                    cleaned[key] = {}
                    paths.append(child_path)
                continue
            child, child_paths = redact_secrets(item, child_path)
            cleaned[key] = child
            paths.extend(child_paths)
        return cleaned, paths
    if isinstance(value, list):
        items: list[Any] = []
        paths = []
        for index, item in enumerate(value):
            child, child_paths = redact_secrets(item, f"{path}[{index}]")
            items.append(child)
            paths.extend(child_paths)
        return items, paths
    return value, []


def validate_bundle_shell(bundle: dict[str, Any], expected_type: str) -> dict[str, Any]:
    def invalid(reason: str, message: str) -> ExternalCatalogError:
        return ExternalCatalogError(
            BizCode.EXTERNAL_CATALOG_INVALID, message, data={"reason": reason}
        )

    if not isinstance(bundle, dict):
        raise invalid("BUNDLE_INVALID", "bundle 必须是对象")
    if len(canonical_json(bundle)) > MAX_MANIFEST_BYTES:
        raise invalid("BUNDLE_TOO_LARGE", "bundle 清单超过 5 MB 上限")
    if bundle.get("schema_version") != 1:
        raise invalid("SCHEMA_VERSION_UNSUPPORTED", "bundle schema_version 必须为 1")
    resource_type = bundle.get("resource_type")
    if not is_supported_type(str(resource_type)) or resource_type != expected_type:
        raise invalid("RESOURCE_TYPE_MISMATCH", "bundle resource_type 不匹配")
    source = bundle.get("source")
    display = bundle.get("display")
    if not isinstance(source, dict) or not isinstance(display, dict):
        raise invalid("BUNDLE_INVALID", "bundle 缺少 source/display")
    if not source.get("local_resource_id"):
        raise invalid("BUNDLE_INVALID", "source 缺少本地资源 ID")
    version = source.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.match(version):
        raise invalid("VERSION_INVALID", "source 版本号格式不合法")
    if not display.get("subtype") or not display.get("publisher_name"):
        raise invalid("BUNDLE_INVALID", "display 缺少 subtype/publisher_name")
    expected_hash = compute_content_hash(bundle)
    if bundle.get("content_hash") != expected_hash:
        raise invalid("CONTENT_HASH_MISMATCH", "bundle content_hash 不一致")
    return bundle


def canonical_request_fingerprint(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body)).hexdigest()
