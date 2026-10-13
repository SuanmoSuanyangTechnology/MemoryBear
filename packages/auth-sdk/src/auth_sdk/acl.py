"""服务间 ACL 匹配：精确 > 通配；deny > allow；默认 deny；段级通配 `*`，不支持正则。"""
import fnmatch
import json
from typing import Protocol


class AclMatcher:
    def __init__(self, rules: list[dict]):
        self.rule_count = len(rules)  # raw entry count, surfaced in reload logs
        # 匹配优先级：精确 > 通配；effect 优先级：deny > allow（同 endpoint 多条规则时 deny 生效）
        self._exact_deny: set[tuple[str, str, str]] = set()
        self._exact_allow: set[tuple[str, str, str]] = set()
        self._wild_deny: list[tuple[str, str, str]] = []
        self._wild_allow: list[tuple[str, str, str]] = []
        for r in rules:
            item = (r["caller"], r["target"], r["endpoint"])
            if "*" in r["endpoint"]:
                (self._wild_deny if r["effect"] == "deny" else self._wild_allow).append(item)
            else:
                (self._exact_deny if r["effect"] == "deny" else self._exact_allow).add(item)

    def allowed(self, caller: str, target: str, endpoint: str) -> bool:
        if (caller, target, endpoint) in self._exact_deny:
            return False
        if (caller, target, endpoint) in self._exact_allow:
            return True
        for c, t, pat in self._wild_deny:
            if c == caller and t == target and fnmatch.fnmatch(endpoint, pat):
                return False
        for c, t, pat in self._wild_allow:
            if c == caller and t == target and fnmatch.fnmatch(endpoint, pat):
                return True
        return False


class AclEvaluator(Protocol):
    """Structural interface for the inbound interceptor's ACL check.

    Satisfied by AclMatcher (static rules) and by AclRefresher (hot-reloadable
    holder), so InboundInterceptor accepts either implementation.
    """

    def allowed(self, caller: str, target: str, endpoint: str) -> bool: ...


def parse_rules(blob: str | bytes | None) -> AclMatcher | None:
    """Strict parse of a pushed rules blob; None = corrupt or wrong shape.

    The hot-reload path must distinguish failure from a deliberate empty rule
    list: "[]" is a valid deny-all state and replaces the current matcher,
    while None tells the caller to keep its rules.
    """
    try:
        rules = json.loads(blob)
    except (TypeError, ValueError):
        return None
    if not isinstance(rules, list):
        return None
    try:
        return AclMatcher(rules)
    except (TypeError, KeyError, AttributeError, IndexError):
        return None


async def load_acl_rules(redis, key: str = "acl:rules") -> "AclMatcher":
    """Fail-safe one-shot load from Redis (any problem -> empty matcher, deny-all).

    Superseded by AclRefresher for hot-reload consumers; kept for plain
    load-once embedders that do not need to tell failure from an empty list.
    """
    try:
        blob = await redis.get(key)
    except Exception:
        return AclMatcher([])
    return parse_rules(blob) or AclMatcher([])
