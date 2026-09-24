"""Statement 三分类器（statement 拆分阶段 2）。

三个自训小模型（gpustack /v1/rerank 打分）为单条 statement 补三个分类字段：

- ``temporal-type-classifier``      → temporal_type（动态/静态/非时间 → DYNAMIC/STATIC/ATEMPORAL）
- ``statement-type-bert``           → statement_type（观点/其他/事实 → OPINION/OTHER/FACT）
- ``unsolved-reference-detector``   → has_unsolved_reference（存在/无未解析指代 → true/false）

按需求（statement.md 第 4 节）：以【单条 statement】为兜底单位——
任一分类任务不可用/超时/非法返回 → 该条三个字段整体走备用 LLM，不混合小模型与 LLM 结果；
其他成功条目不受影响。URL/Key/Model 未配置视为小模型不可用（全量走兜底）。
"""

import asyncio
import logging
import math

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class ClassificationError(RuntimeError):
    """小模型分类失败（不可用/超时/非法返回/映射失败），由调用方决定兜底。"""


class StatementClassification(BaseModel):
    """单条 statement 的三个分类字段（阶段 2 输出）。"""

    statement_type: str = "FACT"
    temporal_type: str = "STATIC"
    has_unsolved_reference: bool = False


# ── 文本标签 → 枚举 映射表（部署侧文案；查不到即非法返回，走兜底，不静默给默认）──
_TEMPORAL_TEXT_MAP = {"动态": "DYNAMIC", "静态": "STATIC", "非时间": "ATEMPORAL"}
_TYPE_TEXT_MAP = {"观点": "OPINION", "其他": "OTHER", "事实": "FACT"}
_REF_TEXT_MAP = {"存在未解析指代": True, "无未解析指代": False}

_STATEMENT_TYPE_VALUES = ("FACT", "OPINION", "OTHER")
_TEMPORAL_TYPE_VALUES = ("STATIC", "DYNAMIC", "ATEMPORAL")


class _FallbackClassificationResponse(BaseModel):
    """备用 LLM 三字段分类的结构化输出。"""

    statement_type: str = Field(..., description="FACT / OPINION / OTHER")
    temporal_type: str = Field(..., description="STATIC / DYNAMIC / ATEMPORAL")
    has_unsolved_reference: bool = Field(..., description="true / false")


class StatementClassifier:
    """阶段 2 三分类器：小模型优先，失败按条整体回退备用 LLM。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._available = bool(settings.FAST_WRITE_EMOTION_URL)

    def _client_sync(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.STATEMENT_CLASSIFIER_TIMEOUT_SECONDS
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── 小模型打分 ──

    async def _score_one(
        self,
        *,
        model: str,
        api_key: str,
        statement_text: str,
    ) -> tuple[str, float]:
        """调用单个小模型，返回 (top1 文本标签, 分数)。非法响应抛 ClassificationError。"""
        if not self._available or not api_key or not model:
            raise ClassificationError(f"classifier unavailable: {model}")
        payload = {"model": model, "query": statement_text, "top_n": 3}
        try:
            resp = await self._client_sync().post(
                settings.FAST_WRITE_EMOTION_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
            raise ClassificationError(f"classifier request failed: {model}: {exc}") from None

        if resp.status_code < 200 or resp.status_code >= 300:
            raise ClassificationError(f"classifier HTTP {resp.status_code}: {model}")

        try:
            data = resp.json()
        except Exception as exc:
            raise ClassificationError(f"classifier response not JSON: {model}") from None

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or not results:
            raise ClassificationError(f"classifier empty results: {model}")

        top: dict | None = None
        top_score: float = -1.0
        for item in results:
            if not isinstance(item, dict):
                continue
            score = item.get("relevance_score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            score = float(score)
            if not math.isfinite(score) or score < 0 or score > 1:
                continue
            doc = item.get("document")
            text = doc.get("text") if isinstance(doc, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            if score > top_score:
                top_score = score
                top = text

        if top is None:
            raise ClassificationError(f"classifier invalid results: {model}")
        return top.strip(), top_score

    # ── 三分类聚合（并发，任一失败抛错由调用方按条兜底）──

    async def classify_with_small_models(
        self, statement_text: str
    ) -> StatementClassification:
        async def _type() -> str:
            text, _ = await self._score_one(
                model=settings.STATEMENT_TYPE_CLASSIFIER_MODEL,
                api_key=settings.STATEMENT_TYPE_CLASSIFIER_API_KEY,
                statement_text=statement_text,
            )
            label = _TYPE_TEXT_MAP.get(text)
            if label is None:
                raise ClassificationError(f"unmapped type label: {text!r}")
            return label

        async def _temporal() -> str:
            text, _ = await self._score_one(
                model=settings.TEMPORAL_TYPE_CLASSIFIER_MODEL,
                api_key=settings.TEMPORAL_TYPE_CLASSIFIER_API_KEY,
                statement_text=statement_text,
            )
            label = _TEMPORAL_TEXT_MAP.get(text)
            if label is None:
                raise ClassificationError(f"unmapped temporal label: {text!r}")
            return label

        async def _ref() -> bool:
            text, _ = await self._score_one(
                model=settings.UNSOLVED_REFERENCE_CLASSIFIER_MODEL,
                api_key=settings.UNSOLVED_REFERENCE_CLASSIFIER_API_KEY,
                statement_text=statement_text,
            )
            label = _REF_TEXT_MAP.get(text)
            if label is None:
                raise ClassificationError(f"unmapped reference label: {text!r}")
            return label

        # 显式建 task：任一分类失败时取消其余仍在飞的请求，避免调用方已进入
        # LLM 兜底后，迟到的分类请求还在后台跑完（gather 默认不取消兄弟任务）。
        tasks = [
            asyncio.create_task(_type()),
            asyncio.create_task(_temporal()),
            asyncio.create_task(_ref()),
        ]
        try:
            stmt_type, temporal_type, has_ref = await asyncio.gather(*tasks)
        except ClassificationError:
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return StatementClassification(
            statement_type=stmt_type,
            temporal_type=temporal_type,
            has_unsolved_reference=has_ref,
        )

    # ── 备用 LLM 兜底（单条整体回退）──

    async def classify_with_fallback_llm(
        self,
        statement_text: str,
        llm_client,
        language: str,
    ) -> StatementClassification:
        from app.core.memory.utils.prompt.prompt_utils import (
            render_statement_classification_fallback_prompt,
        )
        from app.core.memory.storage_services.extraction_engine.steps.base import (
            call_structured,
        )

        prompt = await render_statement_classification_fallback_prompt(
            statement_text=statement_text, language=language
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a statement classifier. Output only valid JSON "
                    "with exactly three fields per the schema."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        parsed = await call_structured(llm_client, messages, _FallbackClassificationResponse)

        stmt_type = (parsed.statement_type or "").strip().upper()
        temporal_type = (parsed.temporal_type or "").strip().upper()
        if stmt_type not in _STATEMENT_TYPE_VALUES:
            raise ClassificationError(f"fallback LLM invalid statement_type: {stmt_type!r}")
        if temporal_type not in _TEMPORAL_TYPE_VALUES:
            raise ClassificationError(f"fallback LLM invalid temporal_type: {temporal_type!r}")
        if not isinstance(parsed.has_unsolved_reference, bool):
            raise ClassificationError("fallback LLM invalid has_unsolved_reference")

        return StatementClassification(
            statement_type=stmt_type,
            temporal_type=temporal_type,
            has_unsolved_reference=parsed.has_unsolved_reference,
        )

    # ── 对外入口：小模型失败 → 整条走备用 LLM；都失败则上抛 ──

    async def classify(
        self,
        statement_text: str,
        llm_client,
        language: str,
    ) -> StatementClassification:
        try:
            return await self.classify_with_small_models(statement_text)
        except Exception:
            logger.info("Statement小模型分类器不可用，进入云端大模型兜底")
        try:
            return await self.classify_with_fallback_llm(
                statement_text, llm_client, language
            )
        except ClassificationError as exc:
            raise ClassificationError(
                f"statement classification failed (small models + fallback LLM): {exc}"
            ) from exc