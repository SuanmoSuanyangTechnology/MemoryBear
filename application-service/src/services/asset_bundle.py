"""Bundle assembly for cross-tenant publication (§7.2 / 接口文档 §8).

Division of labour with the market: **it owns the shell and the validation, we
fill the content**. So this module only produces/consumes the object; it never
invents field names.

Three rules baked in here, each of which had a concrete failure mode:

1. `payload` for an app is `{app_type, config, default_model}` — three levels.
   Flattening the release config makes his `payload.config` read `null`.
2. `content_hash` is computed over the manifest minus the hash itself, with
   sorted keys and `separators=(",", ":")`. Retries must reuse the same bundle:
   a re-export after the source release changed hashes differently and would be
   treated as a different submission.
3. Credential keys are stripped by **exact, case-insensitive key name** and their
   JSON paths recorded in `redacted_secrets`; the values themselves are dropped
   (no placeholder), and the install UI asks the user to re-enter them.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

SCHEMA_VERSION = 1

# 键名精确匹配、大小写不敏感（接口文档 §8.7）。不做子串匹配，否则会误删
# max_tokens 这类普通字段。
SECRET_KEY_NAMES = frozenset(
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

# 跨租户包内不允许出现的私有引用：这些 id 在目标租户里毫无意义，只能通过
# `bindings` 声明为「待重新绑定」。出现即说明导出侧漏了改写。
FORBIDDEN_REF_SUFFIXES = (
    "model_config_id",
    "default_model_config_id",
    "knowledge_base_id",
    "memory_config_id",
    "workspace_id",
    "tenant_id",
)


class BundleError(ValueError):
    """The manifest is structurally invalid or still carries private refs."""


def canonical_json(value: Any) -> str:
    """Sort-key, UTF-8, no extra whitespace — the exact hash input (§8.6)."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_content_hash(manifest: dict[str, Any]) -> str:
    """`sha256:<64 lowercase hex>` over the manifest minus `content_hash`."""

    body = {k: v for k, v in manifest.items() if k != "content_hash"}
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# 展示名不参与依赖复用（接口文档 §8.3）。`display` 整段已被排除，payload 里再
# 带一份同名的 name / display_name 会让「只改了名字」变成另一条市场依赖。
_FINGERPRINT_NAME_KEYS = frozenset({"name", "display_name"})


def _fingerprint_payload(payload: Any) -> dict[str, Any]:
    """Payload copy used for dedup: drop transport file lists and display names."""

    if not isinstance(payload, dict):
        return {}
    copied = copy.deepcopy(payload)
    copied.pop("files", None)
    for key in _FINGERPRINT_NAME_KEYS:
        copied.pop(key, None)
    return copied


def _fingerprint_files(*groups: Any) -> list[dict[str, str]]:
    """File identity is `path + sha256` only, sorted by path (§8.9)."""

    found: dict[str, dict[str, str]] = {}
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            digest = item.get("sha256")
            if not path or not digest:
                continue
            found[str(path)] = {"path": str(path), "sha256": str(digest)}
    return [found[path] for path in sorted(found)]


def _member_digest(member: dict[str, Any]) -> str:
    """Stable hash of one plugin member. Members are not sealed bundles of their own."""

    payload = member.get("payload") if isinstance(member.get("payload"), dict) else {}
    return compute_content_hash(
        {
            "payload": _fingerprint_payload(payload),
            "files": _fingerprint_files(member.get("files"), payload.get("files")),
            "plugin_members": [],
        }
    )


def compute_content_fingerprint(manifest: dict[str, Any]) -> str:
    """Dependency dedup key (§8.9). Not interchangeable with `content_hash`.

    The hashed object is `{payload, files, plugin_members}` only. `display`,
    `source` (including the version string) and `bindings` are excluded, so the
    same content published under another version or another name still matches.
    File object keys are transport addresses and are not part of the identity.
    """

    payload = manifest.get("payload") if isinstance(manifest.get("payload"), dict) else {}
    dependencies = (
        manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}
    )
    members: list[dict[str, str]] = []
    for skill in dependencies.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        ref = str(skill.get("ref") or "")
        if not ref:
            continue
        members.append({"ref": ref, "sha256": _member_digest(skill)})
    members.sort(key=lambda item: item["ref"])
    canonical = {
        "payload": _fingerprint_payload(payload),
        "files": _fingerprint_files(manifest.get("files"), payload.get("files")),
        "plugin_members": members,
    }
    digest = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def seal(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach/replace `content_hash`; mutates and returns the manifest."""

    manifest["content_hash"] = compute_content_hash(manifest)
    return manifest


def verify_content_hash(manifest: dict[str, Any]) -> bool:
    claimed = manifest.get("content_hash")
    return bool(claimed) and claimed == compute_content_hash(manifest)


def redact_secrets(value: Any, path: str = "") -> tuple[Any, list[str]]:
    """Recursively drop secret-named keys; return (sanitised, json_paths).

    Paths use bracket notation for list items so they can be shown to a user:
    `dependencies.tools[0].payload.config.connection_config.headers.Authorization`.
    """

    removed: list[str] = []
    if isinstance(value, dict):
        sanitised: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SECRET_KEY_NAMES:
                removed.append(f"{path}.{key}" if path else str(key))
                continue
            child_path = f"{path}.{key}" if path else str(key)
            child, child_removed = redact_secrets(item, child_path)
            sanitised[key] = child
            removed.extend(child_removed)
        return sanitised, removed
    if isinstance(value, list):
        items = []
        for index, item in enumerate(value):
            child, child_removed = redact_secrets(item, f"{path}[{index}]")
            items.append(child)
            removed.extend(child_removed)
        return items, removed
    return value, removed


def assert_no_private_refs(node: Any, path: str = "manifest") -> None:
    """Reject any surviving tenant/workspace/model/kb/memory reference.

    Called before sealing: a bundle carrying a source-tenant id would either leak
    that id cross-tenant or silently resolve to `None` on install.
    """

    if isinstance(node, dict):
        for key, item in node.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_REF_SUFFIXES and item not in (None, "", []):
                raise BundleError(
                    f"{path}.{key} 仍是源租户私有引用（值 {item!r}），请改写为 bindings"
                )
            assert_no_private_refs(item, f"{path}.{key}")
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            assert_no_private_refs(item, f"{path}[{index}]")


def build_binding(
        *,
        kind: str,
        name: str | None,
        required: bool = True,
        ref: str | None = None,
        **extra: Any,
) -> dict[str, Any]:
    """One `bindings[]` entry.

    `kind` is one of his four (`model` / `knowledge_base` / `sub_agent` /
    `end_user`) plus our `builtin_tool`, which maps to "the target tenant's
    equivalent built-in tool".
    """

    binding: dict[str, Any] = {
        "kind": kind,
        "ref": ref,
        "name": name,
        "required": required,
    }
    binding.update({k: v for k, v in extra.items() if v is not None})
    return binding


def build_app_manifest(
        *,
        local_resource_id: str,
        release_id: str,
        version: str,
        display: dict[str, Any],
        app_type: str,
        config: dict[str, Any],
        default_model: dict[str, Any] | None = None,
        dependencies: dict[str, Any] | None = None,
        bindings: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble one app bundle shell (§8.1) and seal it.

    `config` is the release snapshot **after** redaction and ref rewriting; the
    caller is responsible for both, because only it knows which ids are
    tenant-scoped copies vs. workspace-scoped references.
    """

    deps = {
        "tools": [],
        "skills": [],
        "plugins": [],
        # 我们的扩展：集群子 Agent、工作流 agent 节点引用的 Agent 都随包，
        # 不建自己的市场条目（§7.2.1）
        "apps": [],
    }
    if dependencies:
        deps.update(dependencies)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "resource_type": "app",
        "source": {
            "local_resource_id": local_resource_id,
            "release_id": release_id,
            "version": version,
        },
        "display": display,
        "payload": {
            "app_type": app_type,
            "config": config,
            "default_model": default_model,
        },
        "dependencies": deps,
        "bindings": bindings or [],
        "files": files or [],
        "redacted_secrets": [],
    }
    return seal(manifest)


def finalise_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Redact → assert no private refs → hash.

    Order matters: hashing before redaction would make his verification fail,
    because he recomputes over what he actually received.
    """

    sanitised, removed = redact_secrets(manifest)
    sanitised["redacted_secrets"] = sorted(set(sanitised.get("redacted_secrets") or []) | set(removed))
    assert_no_private_refs(
        {k: v for k, v in sanitised.items() if k not in ("redacted_secrets", "content_hash")}
    )
    return seal(sanitised)


def parse_bundle(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate the shell we receive at install time and verify its hash."""

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError(f"不支持的 schema_version: {manifest.get('schema_version')}")
    for field in ("resource_type", "source", "payload", "dependencies", "content_hash"):
        if field not in manifest:
            raise BundleError(f"bundle 缺少必填字段: {field}")
    if not verify_content_hash(manifest):
        raise BundleError("bundle content_hash 校验失败")
    return manifest


__all__ = [
    "BundleError",
    "SCHEMA_VERSION",
    "assert_no_private_refs",
    "build_app_manifest",
    "build_binding",
    "canonical_json",
    "compute_content_hash",
    "finalise_manifest",
    "parse_bundle",
    "redact_secrets",
    "seal",
    "verify_content_hash",
]