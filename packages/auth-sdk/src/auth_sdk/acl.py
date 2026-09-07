"""服务间 ACL 匹配：精确 > 通配；deny > allow；默认 deny；段级通配 `*`，不支持正则。"""
import fnmatch
import json


class AclMatcher:
    def __init__(self, rules: list[dict]):
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


async def load_acl_rules(redis, key: str = "acl:rules") -> "AclMatcher":
    """从 Redis 加载下发后的规则（identity 变更时全量写 acl:rules，格式见 rules_to_redis）。

    fail-safe：键缺失/超时/损坏 → 空规则（默认全拒）。调用方（未来 InboundInterceptor）
    按需周期性调用以跟随规则变更。
    """
    try:
        blob = await redis.get(key)
    except Exception:
        return AclMatcher([])
    if not blob:
        return AclMatcher([])
    try:
        rules = json.loads(blob)
    except (TypeError, ValueError):
        return AclMatcher([])
    return AclMatcher(rules)
