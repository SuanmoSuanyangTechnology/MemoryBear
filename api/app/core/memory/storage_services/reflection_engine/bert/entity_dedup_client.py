"""实体去重专用模型 HTTP 客户端（GPUStack /model/proxy/predict）。

单轮高频去重内复用一个 ``httpx.AsyncClient``，由调用方在本轮结束时 ``close()``。
调用或响应不合法时抛 ``EntityDedupModelError``，由单对判断模块回退原 LLM。
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


class EntityDedupModelError(RuntimeError):
    """专用模型不可用、调用失败或响应不合法，需要回退原 LLM。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class EntityDedupResult:
    """通过契约校验后，实际参与去重处理的模型结果。"""

    should_merge: bool
    new_name: Optional[str]
    merged_aliases: Optional[List[str]]


def _build_description(summary: Any, description: Any) -> Optional[str]:
    """摘要与描述合并为接口 description；二者都为空时传 null。"""
    summary_text = summary.strip() if isinstance(summary, str) else ""
    description_text = description.strip() if isinstance(description, str) else ""
    if summary_text and description_text:
        return f"身份摘要：{summary_text}\n新增描述：{description_text}"
    return summary_text or description_text or None


def _build_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    name = entity.get("name")
    entity_type = entity.get("entity_type")
    if not isinstance(name, str) or not name.strip():
        raise EntityDedupModelError("invalid_request")
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise EntityDedupModelError("invalid_request")
    aliases = entity.get("aliases")
    if aliases is None:
        aliases = []
    if not isinstance(aliases, (list, tuple)) or any(not isinstance(a, str) for a in aliases):
        raise EntityDedupModelError("invalid_request")
    return {
        "name": name,
        "entity_type": entity_type,
        "aliases": list(aliases),
        "description": _build_description(
            entity.get("description_summary"), entity.get("description")
        ),
    }


def _parse_response(payload: Any, sample_id: str) -> EntityDedupResult:
    """只校验响应归属与实际使用的结果字段；异常时由调用方回退 LLM。"""
    if not isinstance(payload, dict):
        raise EntityDedupModelError("invalid_response")
    if payload.get("sample_id") != sample_id:
        raise EntityDedupModelError("sample_id_mismatch")

    should_merge = payload.get("should_merge")
    if not isinstance(should_merge, bool):
        raise EntityDedupModelError("invalid_should_merge")
    new_name = None
    merged_aliases = None
    if should_merge:
        new_name = payload.get("new_name")
        merged_aliases = payload.get("merged_aliases")
        if not isinstance(new_name, str) or not new_name.strip():
            raise EntityDedupModelError("invalid_new_name")
        if not isinstance(merged_aliases, list) or any(
            not isinstance(a, str) for a in merged_aliases
        ):
            raise EntityDedupModelError("invalid_merged_aliases")

    return EntityDedupResult(
        should_merge=should_merge,
        new_name=new_name,
        merged_aliases=merged_aliases,
    )


class EntityDedupClient:
    """单轮去重共享的专用模型客户端。

    配置缺失时视为服务不可用，不建立连接；``close()`` 关闭本轮连接池。
    """

    def __init__(self, *, url: str, api_key: str, model: str, timeout: float):
        self.url = (url or "").strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def judge_pair(
        self,
        sample_id: str,
        entity_a: Dict[str, Any],
        entity_b: Dict[str, Any],
    ) -> EntityDedupResult:
        if not (self.url and self.api_key and self.model):
            raise EntityDedupModelError("service_unavailable")

        payload = {
            "sample_id": sample_id,
            "A": _build_entity(entity_a),
            "B": _build_entity(entity_b),
        }
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=None)
        try:
            response = await asyncio.wait_for(
                self._http.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-GPUStack-Model": self.model,
                    },
                    json=payload,
                ),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise EntityDedupModelError("model_call_failed") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise EntityDedupModelError("result_parse_failed") from exc
        return _parse_response(data, sample_id)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
