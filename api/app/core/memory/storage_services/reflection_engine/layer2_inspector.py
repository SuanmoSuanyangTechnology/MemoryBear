"""Layer 2 离线巡检 — 统一编排"""
import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.core.memory.storage_services.reflection_engine.deterministic.description_checker import (
    scan_merge_candidates,
)
from app.core.memory.storage_services.reflection_engine.llm.description_synthesizer import (
    merge_description,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import REFLECTION_DESC_UPDATE

logger = logging.getLogger(__name__)


class DescriptionMergeConfig(BaseModel):
    """子问题 6 配置（不需要 enabled 开关，总开关在 task 层）"""
    min_fragments: int = 5
    merge_batch_size: int = 30
    merge_concurrency: int = 5


@dataclass
class ExecutionStep:
    """Pipeline 执行步骤记录"""
    name: str                          # 步骤名称
    type: str                          # 步骤类型：prompt | llm | decide | write
    duration_ms: Optional[int] = None  # 耗时（毫秒）
    output: str = ""                   # 简短输出描述
    success: bool = True               # 是否成功


@dataclass
class ExecutionTracker:
    """反思引擎执行过程追踪器

    记录每个步骤的名称、类型、耗时和输出，最终序列化为 execution_detail JSON
    存入 ReflectionLog 表，供前端 Pipeline 可视化展示。

    用法：
        tracker = ExecutionTracker()
        tracker.start_step("LLM 合并", "llm")
        result = await call_llm(...)
        tracker.end_step("合并完成，120 字符")
        execution_detail = tracker.to_dict()
    """
    steps: List[ExecutionStep] = field(default_factory=list)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    _start_time: float = 0.0

    def start_step(self, name: str, step_type: str):
        self._start_time = time.perf_counter()
        self.steps.append(ExecutionStep(name=name, type=step_type))

    def end_step(self, output: str = "", success: bool = True):
        step = self.steps[-1]
        step.duration_ms = int((time.perf_counter() - self._start_time) * 1000)
        step.output = output
        step.success = success

    def to_dict(self) -> dict:
        total_ms = sum(s.duration_ms or 0 for s in self.steps)
        return {
            "steps": [asdict(s) for s in self.steps],
            "total_ms": total_ms,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


class Layer2Inspector:
    def __init__(self, neo4j_connector: Neo4jConnector, llm_client: Any,
                 log_repo: Any, config: Optional[Dict[str, Any]] = None):
        self.connector = neo4j_connector
        self.llm_client = llm_client
        self.log_repo = log_repo
        self.desc_config = DescriptionMergeConfig(**(config or {}))
        self._semaphore = asyncio.Semaphore(self.desc_config.merge_concurrency)

    async def run(self, end_user_id: str, baseline: str = "HYBRID",
                  language: str = "zh") -> Dict[str, Any]:
        """执行 Layer 2 巡检

        执行顺序按架构设计：1→2→5→3→6→4
        当前只实现子问题 6，其他预留 TODO。
        """
        results = {}

        # TODO: 子问题 1 — 过期检测（stale_detection）
        # TODO: 子问题 2 — 事实矛盾检测（fact_contradiction）
        # TODO: 子问题 5 — 未识别实体处理（unresolved_entity）
        # TODO: 子问题 3 — 复杂去重消歧（entity_dedup）

        # 子问题 6 — 描述合并（已实现）
        results["description_merge"] = await self._run_description_merge(
            end_user_id, baseline, language
        )

        # TODO: 子问题 4 — 本体 Metadata 校验（metadata_validation）

        return results

    async def _run_description_merge(self, end_user_id: str, baseline: str,
                                     language: str) -> Dict[str, Any]:
        """子问题 6：描述合并（并发控制）"""
        candidates = await scan_merge_candidates(
            self.connector, end_user_id,
            min_fragments=self.desc_config.min_fragments,
            batch_size=self.desc_config.merge_batch_size,
        )
        if not candidates:
            return {"status": "success", "candidate_count": 0, "merged_count": 0}

        async def _merge_with_limit(entity):
            async with self._semaphore:
                return await self._merge_one_entity(entity, end_user_id, baseline, language)

        tasks = [_merge_with_limit(e) for e in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged_count = sum(1 for r in results if r is True)

        return {
            "status": "success",
            "candidate_count": len(candidates),
            "merged_count": merged_count,
        }

    async def _merge_one_entity(self, entity: Dict, end_user_id: str,
                                baseline: str, language: str) -> bool:
        """对单个实体执行描述合并"""
        tracker = ExecutionTracker()
        description = entity["description"]
        existing_summary = entity.get("description_summary")
        existing_timeline = entity.get("description_timeline")

        fragments = [f.strip() for f in description.split('；') if f.strip()]
        if len(fragments) < self.desc_config.min_fragments:
            return False

        # Step 1: 碎片拆分 + 备份 timeline
        tracker.start_step("碎片拆分+备份", "prompt")
        if existing_timeline:
            timeline = existing_timeline + "；" + description
        else:
            timeline = description
        tracker.end_step(f"{len(fragments)} 条碎片")

        # Step 2: LLM 合并
        tracker.start_step("LLM 合并", "llm")
        merged_text = await merge_description(
            llm_client=self.llm_client,
            entity_name=entity["name"],
            entity_type=entity["entity_type"],
            summary=existing_summary,       # 首次为 None，模板自动判断
            fragments=fragments,
            language=language,
        )
        if not merged_text:
            tracker.end_step("LLM 调用失败", success=False)
            return False
        tracker.end_step(f"合并完成，{len(merged_text)} 字符")

        # Step 3: 写入 Neo4j（清空 description，写 summary + timeline）
        tracker.start_step("写入", "write")
        await self.connector.execute_query(
            REFLECTION_DESC_UPDATE,
            entity_id=entity["entity_id"],
            summary=merged_text,
            timeline=timeline,
        )
        tracker.end_step("写入完成")

        # 写 ReflectionLog
        self.log_repo.create(
            end_user_id=end_user_id,
            sub_problem="description_merge",
            trigger_type="scheduled",
            baseline=baseline,
            strategy="MERGE",
            confidence=None,                # 描述合并不需要 confidence
            status="resolved",
            summary_text=f'{entity["name"]}: 合并 {len(fragments)} 条碎片',
            entity_ids=[entity["entity_id"]],
            trigger_detail={
                "entity_id": entity["entity_id"],
                "entity_name": entity["name"],
                "original_description": description,
                "fragment_count": len(fragments),
            },
            solution_detail={
                "title": "MERGE — 合并描述碎片为摘要",
                "changes": [
                    {"field": "description", "old": description, "new": ""},
                    {"field": "description_summary", "old": existing_summary or "", "new": merged_text},
                ],
            },
            execution_detail=tracker.to_dict(),
        )
        return True