"""通过 rerank 分类接口逐对判断反思阶段的别名候选。"""

import asyncio
import json
import math
import time
from typing import Any

import httpx

from app.core.memory.storage_services.reflection_engine.errors import ReflectionFailureReason

FIELDS = (
    "alias_name", "alias_description", "alias_description_summary", "alias_aliases",
    "target_name", "target_description", "target_description_summary", "target_aliases",
)


def build_query(candidate: dict[str, Any]) -> str:
    """保留八个独立字段；非法名称或 aliases 留给调用方暂缓。"""
    data: dict[str, Any] = {}
    for key in FIELDS:
        value = candidate.get(key)
        if key.endswith("_aliases"):
            if value is None:
                value = []
            if not isinstance(value, (list, tuple)) or any(not isinstance(a, str) for a in value):
                raise ValueError("invalid_aliases")
            data[key] = list(value)
        else:
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("invalid_text")
            if key in ("alias_name", "target_name") and not value.strip():
                raise ValueError("empty_name")
            data[key] = value
    return json.dumps(data, ensure_ascii=False)


def parse_response(payload: Any) -> dict[str, Any]:
    """按 index 与标签双重校验，不依赖结果排序。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid_results")
    rows = payload["results"]
    if len(rows) != 2:
        raise ValueError("invalid_results_count")
    scores: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_result")
        index = row.get("index")
        document = row.get("document")
        score = row.get("relevance_score")
        if type(index) is not int or index not in (0, 1):
            raise ValueError("invalid_index")
        label = document.get("text") if isinstance(document, dict) else None
        if label != ("merge", "drop")[index] or label in scores:
            raise ValueError("invalid_label")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("invalid_score")
        score = float(score)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid_score")
        scores[label] = score
    if set(scores) != {"merge", "drop"}:
        raise ValueError("missing_label")
    if scores["merge"] == scores["drop"]:
        return {"status": "deferred", "failure_reason": "ambiguous_result", **{f"{k}_score": v for k, v in scores.items()}}
    decision = "merge" if scores["merge"] > scores["drop"] else "drop"
    return {
        "status": "decided", "decision": decision,
        "confidence": scores["merge"],
        "merge_score": scores["merge"], "drop_score": scores["drop"],
        "score_kind": "rerank_relevance_score", "decision_source": "bert",
        "reason": f"BERT 判定为 {decision}；merge_score={scores['merge']:.6f}，drop_score={scores['drop']:.6f}",
    }


class AliasRecognitionClient:
    def __init__(self, *, url: str, api_key: str, model: str, timeout: float, concurrency: int):
        if not math.isfinite(timeout) or timeout <= 0 or concurrency <= 0:
            raise ValueError("timeout and concurrency must be positive")
        self.url, self.api_key, self.model = url.strip(), api_key.strip(), model.strip()
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(concurrency)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self):
        if self.url and self.api_key and self.model:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self

    async def __aexit__(self, *_exc):
        if self._http is not None:
            await self._http.aclose()

    async def judge(self, candidate: dict[str, Any]) -> dict[str, Any]:
        identity = {"alias_id": candidate.get("alias_id"), "target_id": candidate.get("target_id")}
        if not self.url or not self.api_key or not self.model:
            return {**identity, "status": "failed", "failure_reason": "service_unavailable",
                    "reason_code": ReflectionFailureReason.REFLECTION_MODEL_UNAVAILABLE.value}
        try:
            query = build_query(candidate)
        except ValueError as exc:
            return {**identity, "status": "deferred", "failure_reason": str(exc)}
        async with self.semaphore:
            started = time.perf_counter()
            try:
                assert self._http is not None
                response = await asyncio.wait_for(self._http.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "query": query, "documents": ["merge", "drop"],
                          "top_n": 2, "return_documents": True},
                ), timeout=self.timeout)
                response.raise_for_status()
            except (httpx.HTTPError, asyncio.TimeoutError):
                return {**identity, "status": "failed", "failure_reason": "model_call_failed",
                        "reason_code": ReflectionFailureReason.MODEL_CALL_FAILED.value,
                        "request_ms": int((time.perf_counter() - started) * 1000)}
            except Exception:
                # 单条请求的意外客户端错误也仅影响该候选；不暴露响应或凭据。
                return {**identity, "status": "failed", "failure_reason": "model_call_failed",
                        "reason_code": ReflectionFailureReason.MODEL_CALL_FAILED.value,
                        "request_ms": int((time.perf_counter() - started) * 1000)}
            try:
                parsed = parse_response(response.json())
            except (ValueError, TypeError):
                return {**identity, "status": "failed", "failure_reason": "result_parse_failed",
                        "reason_code": ReflectionFailureReason.RESULT_PARSE_FAILED.value,
                        "request_ms": int((time.perf_counter() - started) * 1000)}
        return {**identity, **parsed, "model_name": self.model,
                "request_ms": int((time.perf_counter() - started) * 1000)}

    async def judge_all(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """TaskGroup 在预算取消时会取消并等待全部请求退出。"""
        tasks: list[asyncio.Task] = []
        async with asyncio.TaskGroup() as group:
            for candidate in candidates:
                tasks.append(group.create_task(self.judge(candidate)))
        return [task.result() for task in tasks]
