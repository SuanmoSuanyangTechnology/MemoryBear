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
from app.core.memory.storage_services.reflection_engine.deterministic.unresolved_scanner import (
    scan_unresolved_candidates,
    fetch_context_chunks,
)
from app.core.memory.storage_services.reflection_engine.llm.description_synthesizer import (
    merge_description,
    summarize_extract_and_rename,
    validate_summary_output,
    filter_events,
)
from app.core.memory.storage_services.reflection_engine.llm.unresolved_resolver import (
    resolve_unresolved_statement,
    validate_unresolved_output,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.cypher_queries import (
    REFLECTION_DESC_UPDATE,
    REFLECTION_RENAME_CHECK_CONFLICT,
    REFLECTION_RENAME_ENTITY,
    REFLECTION_UPDATE_NAME_EMBEDDING,
    UNRESOLVED_CREATE_ENTITY,
    UNRESOLVED_UPDATE_NAME_EMBEDDING,
    UNRESOLVED_CREATE_RELATIONSHIP,
    UNRESOLVED_CREATE_STATEMENT_ENTITY_EDGE,
    UNRESOLVED_UPDATE_STATEMENT_FLAG,
)

logger = logging.getLogger(__name__)


class DescriptionMergeConfig(BaseModel):
    """子问题 6 实体描述合并配置"""
    min_fragments: int = 5              # 碎片数阈值（≥此值才触发合并）
    merge_batch_size: int = 30          # 每批最多处理实体数
    merge_concurrency: int = 5          # LLM 并发数


class EntityDedupConfig(BaseModel):
    """子问题 3 去重消歧配置"""
    # === 方案A：高频两路召回 ===
    candidate_cap_name: int = 500       # 路径A最大候选数
    candidate_cap_embed: int = 500      # 路径B最大候选数
    top_k_embed: int = 100              # 向量索引top-K（需穿透跨用户干扰）
    theta_embed_floor: float = 0.85     # 向量初筛阈值
    alpha: float = 0.4                  # 名称权重
    beta: float = 0.6                   # 向量权重
    theta_low: float = 0.70             # 丢弃阈值（P≤此值写Redis缓存）
    llm_merge_threshold: float = 0.85   # LLM确认后合并阈值
    max_merges_per_run: int = 20        # 单次最多合并数
    merge_concurrency: int = 5          # LLM并发数

    # === 方案B：低频分组 LLM ===
    min_entities_for_scan: int = 3      # 少于此数不扫描
    max_pairs_per_run: int = 20         # 单次最多合并对数


class ReflectionConfig(BaseModel):
    """反思引擎统一配置"""
    # === 基础 ===
    enabled: bool = True                # 是否启用反思引擎
    language: str = "zh"                # 语言：zh / en
    baseline: str = "HYBRID"            # 反思基线：TIME / FACT / HYBRID

    # === 子问题配置（嵌套） ===
    # 子问题 3 — 复杂去重消歧（entity_dedup）
    entity_dedup: EntityDedupConfig = EntityDedupConfig()
    # 子问题 6 — 描述合并
    description_merge: DescriptionMergeConfig = DescriptionMergeConfig()
    # stale_detection: StaleDetectionConfig = StaleDetectionConfig()        # 待实现
    # fact_contradiction: FactContradictionConfig = ...                     # 待实现
    # metadata_validation: MetadataValidationConfig = ...                   # 待实现
    # unresolved_entity: UnresolvedEntityConfig = ...                       # 待实现

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
        tracker = ExecutionTracker(model="qwen-plus")
        tracker.start_step("LLM 合并", "llm")
        result = await call_llm(...)
        tracker.end_step("合并完成，120 字符")
        execution_detail = tracker.to_dict()
    """
    steps: List[ExecutionStep] = field(default_factory=list)
    model: str = ""
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
        }


class Layer2Inspector:
    def __init__(self, neo4j_connector: Neo4jConnector, llm_client: Any,
                 log_repo_factory: Any, embedding_client: Any = None,
                 config: Optional[Dict[str, Any]] = None):
        self.connector = neo4j_connector
        self.llm_client = llm_client
        self.log_repo_factory = log_repo_factory
        self.embedding_client = embedding_client

        # 统一配置
        self.config = ReflectionConfig(**(config or {}))
        self.desc_config = self.config.description_merge
        self.dedup_config = self.config.entity_dedup
        self._semaphore = asyncio.Semaphore(self.desc_config.merge_concurrency)

    async def run(self, end_user_id: str, baseline: str = "HYBRID",
                  language: str = "zh") -> Dict[str, Any]:
        """执行 Layer 2 巡检

        执行顺序按架构设计：1→2→5→3→6→4
        当前已实现子问题 3（去重）和 6（描述合并），其他预留。
        """
        results = {}

        # TODO: 子问题 1 — 过期检测（stale_detection）
        # TODO: 子问题 2 — 事实矛盾检测（fact_contradiction）
       
        # 子问题 5 — 未识别实体处理（unresolved_entity）
        results["unresolved_entity"] = await self._run_unresolved_resolver(
            end_user_id, baseline, language
        )

        # 子问题 3 — 复杂去重消歧（entity_dedup）
        results["entity_dedup"] = await self._run_entity_dedup(end_user_id, baseline)

        # 子问题 6 — 描述合并
        results["description_merge"] = await self._run_description_merge(
            end_user_id, baseline, language
        )

        # TODO: 子问题 4 — 本体 Metadata 校验（metadata_validation）

        return results

    async def _run_entity_dedup(self, end_user_id: str, baseline: str) -> Dict[str, Any]:
        """子问题 3 复杂去重 方案A：高频两路召回去重"""
        from .deterministic.entity_similarity import (
            fetch_name_candidates, fetch_embed_candidates,
            merge_and_score, partition_by_probability,
        )
        from .deterministic.discard_cache import filter_discarded, cache_discarded
        from .llm.entity_dedup_judge import judge_batch
        from .deterministic.cypher_merger import choose_keeper, execute_merge, build_merged_aliases

        config = self.dedup_config

        # 1. 两路候选召回（并行）— 计时
        t0 = time.perf_counter()
        name_cands, embed_cands = await asyncio.gather(
            fetch_name_candidates(self.connector, end_user_id, config.candidate_cap_name),
            fetch_embed_candidates(self.connector, end_user_id, config.top_k_embed,
                                config.theta_embed_floor, config.candidate_cap_embed),
        )
        recall_ms = int((time.perf_counter() - t0) * 1000)

        # 2. 合并 + 归一化打分 — 计时
        t0 = time.perf_counter()
        candidates = merge_and_score(name_cands, embed_cands, config.alpha, config.beta)
        score_ms = int((time.perf_counter() - t0) * 1000)

        # 3. 过滤丢弃缓存
        candidates = await filter_discarded(end_user_id, candidates)
        # 4. 两档分流（去掉自动合并，全部走 LLM）
        llm_pool, discard_pool = partition_by_probability(
            candidates, config.theta_low)
        await cache_discarded(end_user_id, discard_pool)

        # 5. LLM 判定（所有候选均需 LLM 确认）— 计时
        t0 = time.perf_counter()
        llm_results = await judge_batch(
            self.llm_client, llm_pool,
            config.merge_concurrency,
        )
        llm_ms = int((time.perf_counter() - t0) * 1000)

        # 均摊耗时（每对候选分摊批量耗时）
        n = max(len(llm_pool), 1)
        step_timing = {
            "recall_ms": recall_ms // n,
            "score_ms": score_ms // n,
            "llm_ms": llm_ms // n,
        }

        # 6. 合并执行（全部经 LLM 确认后才合并）
        merged_count = 0
        recorded_count = 0
        rejected_pairs = []
        for pair, decision in llm_results:
            if merged_count >= config.max_merges_per_run:
                break
            if decision and decision.same_entity and decision.confidence >= config.llm_merge_threshold:
                success = await self._apply_dedup_merge(pair, end_user_id, baseline, llm_decision=decision, step_timing=step_timing)
                if success:
                    merged_count += 1
            else:
                # LLM 拒绝或 confidence 不够 → 收集待写缓存 + 写 recorded 日志
                rejected_pairs.append(pair)
                reason = decision.reason if decision else "LLM 判定失败"
                conf = decision.confidence if decision else 0.0
                entity_a = {"entity_id": pair.a_id, "name": pair.a_name, "entity_type": pair.entity_type,
                            "description": pair.a_desc, "aliases": pair.a_aliases}
                entity_b = {"entity_id": pair.b_id, "name": pair.b_name, "entity_type": pair.entity_type,
                            "description": pair.b_desc, "aliases": pair.b_aliases}
                self._write_dedup_log(
                    end_user_id=end_user_id,
                    keeper=entity_a, loser=entity_b,
                    entity_type=pair.entity_type,
                    merged_name="", merged_aliases=[],
                    confidence=conf,
                    execution_detail={
                        "steps": [
                            {"name": "候选召回", "type": "prompt", "duration_ms": step_timing.get("recall_ms"),
                             "output": f"sim_name={pair.sim_name:.2f}, sim_embed={pair.sim_embed:.2f}", "success": True},
                            {"name": "综合打分", "type": "decide", "duration_ms": step_timing.get("score_ms"),
                             "output": f"P={pair.probability:.2f}", "success": True},
                            {"name": "LLM 判定", "type": "llm", "duration_ms": step_timing.get("llm_ms"),
                             "output": f"same_entity=False, confidence={conf:.2f}", "success": True},
                        ],
                        "total_ms": sum(v for v in step_timing.values() if v),
                        "model": getattr(self.llm_client, "model_name", ""),
                    },
                    reason=reason,
                    status="recorded", strategy="NO_OP",
                    baseline=baseline,
                )
                recorded_count += 1

        # 批量写入丢弃缓存（一次 Redis 往返）
        if rejected_pairs:
            await cache_discarded(end_user_id, rejected_pairs)

        return {
            "status": "success",
            "candidate_count": len(candidates),
            "llm_pool": len(llm_pool),
            "discard_pool": len(discard_pool),
            "merged_count": merged_count,
            "recorded_count": recorded_count,
        }


    async def run_dedup_full_scan(self, end_user_id: str) -> Dict[str, Any]:
        """子问题 3 复杂去重 方案B：低频全量扫描去重（公共入口）"""
        return await self._run_dedup_full_scan(end_user_id)

    async def _run_dedup_full_scan(self, end_user_id: str) -> Dict[str, Any]:
        """子问题 3 复杂去重 方案B：低频全量扫描去重"""
        from .deterministic.full_scan_dedup import (
            get_entity_types, get_last_scan_time, check_new_entities,
            fetch_entities_by_type, update_scan_time,
        )
        from .llm.entity_dedup_batch_judge import judge_batch_dedup
        from .deterministic.cypher_merger import execute_merge, build_merged_aliases

        config = self.dedup_config
        total_merged = 0
        scanned_types = 0

        entity_types = await get_entity_types(self.connector, end_user_id)

        for type_row in entity_types:
            entity_type = type_row["entity_type"]
            count = type_row["count"]

            if count < config.min_entities_for_scan:
                continue

            last_time = await get_last_scan_time(end_user_id, entity_type)
            if last_time:
                new_count = await check_new_entities(
                    self.connector, end_user_id, entity_type, last_time)
                if new_count == 0:
                    continue

            scanned_types += 1
            entities = await fetch_entities_by_type(self.connector, end_user_id, entity_type)

            # LLM 分组判定 — 计时
            t0 = time.perf_counter()
            pairs = await judge_batch_dedup(self.llm_client, entities, entity_type)
            llm_ms = int((time.perf_counter() - t0) * 1000)
            llm_ms_per_pair = llm_ms // max(len(pairs), 1)

            merged_count = 0
            for idx_a, idx_b, conf, reason in pairs:
                if merged_count >= config.max_pairs_per_run:
                    break
                if idx_a == idx_b:
                    continue  # 跳过无效对（同一实体）

                ea, eb = entities[idx_a], entities[idx_b]
                if ea["entity_id"] == eb["entity_id"]:
                    continue  # 跳过同 ID 实体

                # confidence 阈值检查（和方案A一致）
                if conf < config.llm_merge_threshold:
                    continue
                keeper, loser = ea, eb
                merged_name = keeper["name"]
                merged_aliases = build_merged_aliases(keeper, loser, merged_name)

                t1 = time.perf_counter()
                success = await execute_merge(
                    self.connector, end_user_id,
                    keeper["entity_id"], loser["entity_id"],
                    merged_name, merged_aliases,
                )
                write_ms = int((time.perf_counter() - t1) * 1000)

                if success:
                    merged_count += 1
                    # 写 ReflectionLog
                    self._write_dedup_log(
                        end_user_id=end_user_id,
                        keeper=keeper, loser=loser,
                        entity_type=entity_type,
                        merged_name=merged_name,
                        merged_aliases=merged_aliases,
                        confidence=conf,
                        reason=reason,
                        execution_detail={
                            "steps": [
                                {"name": "LLM 分组判定", "type": "llm", "duration_ms": llm_ms_per_pair,
                                 "output": f"confidence={conf:.2f}", "success": True},
                                {"name": "选择 keeper", "type": "decide", "duration_ms": 0,
                                 "output": f"keeper={keeper['name']}", "success": True},
                                {"name": "写入", "type": "write", "duration_ms": write_ms,
                                 "output": "合并完成", "success": True},
                            ],
                            "total_ms": llm_ms_per_pair + write_ms,
                            "model": getattr(self.llm_client, "model_name", ""),
                        },
                    )

            total_merged += merged_count
            await update_scan_time(end_user_id, entity_type)

        return {"scanned_types": scanned_types, "merged_count": total_merged}


    def _write_dedup_log(self, end_user_id: str, keeper: Dict, loser: Dict,
                         entity_type: str, merged_name: str, merged_aliases: List,
                         confidence: float, execution_detail: Dict, reason: str = "",
                         status: str = "resolved", strategy: str = "MERGE",
                         baseline: str = "HYBRID"):
        """写去重 ReflectionLog（方案A和B共用，支持 resolved/recorded）"""
        if status == "resolved":
            changes = [c for c in [
                {"field": "name", "old": keeper["name"], "new": merged_name},
                {"field": "aliases",
                 "old": ", ".join(sorted(keeper.get("aliases") or [])),
                 "new": ", ".join(sorted(merged_aliases))},
                {"field": "description",
                 "old": keeper.get("description", ""),
                 "new": f"{keeper.get('description', '')}；{loser.get('description', '')}"},
            ] if c["old"] != c["new"]]
            summary = f'"{keeper["name"]}" ≈ "{loser["name"]}" → 合并'
            title = f"MERGE — LLM确认（confidence={confidence:.2f}）" if confidence else "MERGE"
        else:
            changes = []
            summary = f'"{keeper["name"]}" ≈ "{loser["name"]}" → 未合并'
            title = f"NO_OP — LLM判定不合并（confidence={confidence:.2f}）"

        trigger = {
            "entity_a": {"entity_id": keeper.get("entity_id") or keeper.get("id"),
                         "name": keeper["name"], "entity_type": entity_type,
                         "description": keeper.get("description", ""),
                         "aliases": keeper.get("aliases") or []},
            "entity_b": {"entity_id": loser.get("entity_id") or loser.get("id"),
                         "name": loser["name"], "entity_type": entity_type,
                         "description": loser.get("description", ""),
                         "aliases": loser.get("aliases") or []},
        }
        if reason:
            trigger["reason"] = reason[:200]

        log_repo = self.log_repo_factory()
        log_repo.create(
            end_user_id=end_user_id,
            sub_problem="entity_dedup",
            trigger_type="scheduled",
            baseline=baseline,
            strategy=strategy,
            confidence=confidence,
            status=status,
            summary_text=summary,
            entity_ids=[keeper.get("entity_id") or keeper.get("id"),
                        loser.get("entity_id") or loser.get("id")],
            trigger_detail=trigger,
            solution_detail={"title": title, "changes": changes},
            execution_detail=execution_detail,
        )
        
    async def _apply_dedup_merge(self, pair, end_user_id: str, baseline: str,
                                llm_decision=None, step_timing=None) -> bool:
        """执行单对合并 + 写 ReflectionLog"""
        from .deterministic.cypher_merger import choose_keeper, execute_merge, build_merged_aliases

        tracker = ExecutionTracker(model=getattr(self.llm_client, "model_name", ""))
        timing = step_timing or {}

        # Step 1: 候选召回（均摊耗时）
        tracker.steps.append(ExecutionStep(
            name="候选召回", type="prompt", duration_ms=timing.get("recall_ms"),
            output=f"sim_name={pair.sim_name:.2f}, sim_embed={pair.sim_embed:.2f}", success=True,
        ))

        # Step 2: 综合打分（均摊耗时）
        tracker.steps.append(ExecutionStep(
            name="综合打分", type="decide", duration_ms=timing.get("score_ms"),
            output=f"P={pair.probability:.2f}", success=True,
        ))

        # Step 3: LLM 调用（均摊耗时）
        tracker.steps.append(ExecutionStep(
            name="LLM 判定", type="llm", duration_ms=timing.get("llm_ms"),
            output=f"same_entity={llm_decision.same_entity}, confidence={llm_decision.confidence:.2f}" if llm_decision else "跳过",
            success=bool(llm_decision and llm_decision.same_entity),
        ))

        # Step 4: 策略决策（选择 keeper）
        tracker.start_step("选择 keeper", "decide")
        entity_a = {"entity_id": pair.a_id, "name": pair.a_name, "entity_type": pair.entity_type,
                    "description": pair.a_desc, "aliases": pair.a_aliases}
        entity_b = {"entity_id": pair.b_id, "name": pair.b_name, "entity_type": pair.entity_type,
                    "description": pair.b_desc, "aliases": pair.b_aliases}
        winner = llm_decision.winner_id if llm_decision else None
        keeper, loser = choose_keeper(entity_a, entity_b, winner)
        merged_name = llm_decision.merged_name if llm_decision and llm_decision.merged_name else keeper["name"]
        merged_aliases = build_merged_aliases(keeper, loser, merged_name)
        tracker.end_step(f"keeper={keeper['name']}")

        # Step 5: 写入
        tracker.start_step("写入", "write")
        success = await execute_merge(
            self.connector, end_user_id,
            keeper["entity_id"], loser["entity_id"],
            merged_name, merged_aliases,
        )
        if not success:
            tracker.end_step("合并失败", success=False)
            return False
        tracker.end_step("合并完成")

        # Step 6: 写 ReflectionLog
        self._write_dedup_log(
            end_user_id=end_user_id,
            keeper=keeper, loser=loser,
            entity_type=pair.entity_type,
            merged_name=merged_name,
            merged_aliases=merged_aliases,
            confidence=llm_decision.confidence if llm_decision else pair.probability,
            execution_detail=tracker.to_dict(),
            reason=llm_decision.reason if llm_decision else "",
            baseline=baseline,
        )
        return True

    #子问题6：实体描述合并
    async def _run_unresolved_resolver(self, end_user_id: str, baseline: str,
                                       language: str) -> Dict[str, Any]:
        """子问题 5：未识别实体处理（并发控制）"""
        candidates = await scan_unresolved_candidates(
            self.connector, end_user_id, batch_size=30
        )
        if not candidates:
            return {"status": "success", "total": 0, "resolved": 0, "forced": 0}

        async def _resolve_with_limit(stmt):
            async with self._semaphore:
                return await self._resolve_one_statement(
                    stmt, end_user_id, baseline, language
                )

        tasks = [_resolve_with_limit(stmt) for stmt in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        resolved_count = 0
        forced_count = 0
        failed_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"未识别实体处理异常 statement={candidates[i].get('statement_id', '?')}: {result}"
                )
                failed_count += 1
            elif result is None:
                failed_count += 1
            elif result:
                resolved_count += 1
            else:
                forced_count += 1

        return {
            "status": "success",
            "total": len(candidates),
            "resolved": resolved_count,
            "forced": forced_count,
            "failed": failed_count,
        }

    async def _resolve_one_statement(self, stmt: Dict, end_user_id: str,
                                     baseline: str, language: str) -> Optional[bool]:
        """处理单条 unresolved statement

        Returns:
            True = 消解成功, False = 强制提取, None = 失败（不改标记）
        """
        tracker = ExecutionTracker(model=getattr(self.llm_client, "model_name", ""))

        # Step 1: 获取上下文
        tracker.start_step("上下文收集", "prompt")
        context_chunks = await fetch_context_chunks(
            self.connector,
            chunk_id=stmt.get("chunk_id"),
            end_user_id=end_user_id,
            limit=10,
        )
        tracker.end_step(f"获取 {len(context_chunks)} 条 Chunk 上下文")

        # Step 2: LLM 消解 + 提取
        tracker.start_step("LLM 消解+提取", "llm")
        result = await resolve_unresolved_statement(
            llm_client=self.llm_client,
            statement=stmt,
            context_chunks=context_chunks,
            language=language,
        )
        if result is None:
            tracker.end_step("LLM 调用失败", success=False)
            return None
        tracker.end_step(
            f"resolved={result.resolved}, entities={len(result.entities)}, "
            f"triplets={len(result.triplets)}"
        )

        # Step 3: 校验
        tracker.start_step("校验", "decide")
        validated = validate_unresolved_output(result)
        if not validated.valid:
            tracker.end_step(f"校验失败: {validated.reason}", success=False)
            logger.warning(
                f"未识别实体校验失败 statement={stmt['statement_id']}, "
                f"reason={validated.reason}"
            )
            return None
        tracker.end_step(
            f"有效实体 {len(validated.entities)}, 有效 triplet {len(validated.triplets)}"
        )

        # Step 4: 写入 Neo4j
        tracker.start_step("写入Neo4j", "write")
        created_entity_ids = []

        # 4.1 创建实体
        for entity in validated.entities:
            # 跳过"用户"实体（用户节点已存在，不需要重复创建）
            if entity.name.strip() == "用户":
                continue
            entity_result = await self.connector.execute_query(
                UNRESOLVED_CREATE_ENTITY,
                end_user_id=end_user_id,
                name=entity.name,
                entity_type=entity.type,
                description=entity.description,
            )
            if entity_result:
                entity_id = entity_result[0].get("entity_id", "")
                created_entity_ids.append(entity_id)
                # 补 name_embedding
                if self.embedding_client:
                    try:
                        name_embedding = self.embedding_client.embed_query(entity.name)
                        if name_embedding:
                            await self.connector.execute_query(
                                UNRESOLVED_UPDATE_NAME_EMBEDDING,
                                entity_id=entity_id,
                                name_embedding=name_embedding,
                            )
                    except Exception as emb_err:
                        logger.warning(f"补 name_embedding 失败 entity={entity.name}: {emb_err}")

        # 4.2 创建关系边
        for triplet in validated.triplets:
            try:
                await self.connector.execute_query(
                    UNRESOLVED_CREATE_RELATIONSHIP,
                    end_user_id=end_user_id,
                    subject_name=triplet.subject_name,
                    object_name=triplet.object_name,
                    predicate=triplet.predicate,
                    predicate_id=triplet.predicate_id,
                    predicate_surface=triplet.predicate_surface,
                    statement_id=stmt["statement_id"],
                    valid_at=triplet.valid_at,
                    invalid_at=triplet.invalid_at,
                )
            except Exception as rel_err:
                logger.warning(f"创建关系边失败: {rel_err}")

        # 4.3 创建 REFERENCES_ENTITY 边
        for entity in validated.entities:
            await self.connector.execute_query(
                UNRESOLVED_CREATE_STATEMENT_ENTITY_EDGE,
                statement_id=stmt["statement_id"],
                end_user_id=end_user_id,
                entity_name=entity.name,
            )

        # 4.4 更新 Statement 标记
        await self.connector.execute_query(
            UNRESOLVED_UPDATE_STATEMENT_FLAG,
            statement_id=stmt["statement_id"],
        )
        tracker.end_step(
            f"创建 {len(validated.entities)} 实体, "
            f"{len(validated.triplets)} 关系边, 标记已更新"
        )

        # Step 5: 写反思日志（仅 resolved=true 时）
        if validated.resolved:
            # 过滤掉"用户"实体（与 Step 4 写入逻辑一致：用户节点不是被消解出的新实体）
            resolved_entities = [
                e for e in validated.entities if e.name.strip() != "用户"
            ]

            # 列表页摘要：消解指代: <首个实体> 等 N 个实体
            if resolved_entities:
                summary_text = (
                    f"消解指代: {resolved_entities[0].name} "
                    f"等 {len(resolved_entities)} 个实体"
                )
            else:
                summary_text = "消解指代: 无新增实体"

            # 详情页变更项：按实体逐行展示，附带实体类型
            changes = [
                {
                    "field": "识别实体",
                    "old": "未识别",
                    "new": f"{e.name}（{e.type}）",
                }
                for e in resolved_entities
            ]

            log_repo = self.log_repo_factory()
            log_repo.create(
                end_user_id=end_user_id,
                sub_problem="unresolved_entity",
                trigger_type="scheduled",
                baseline=baseline,
                strategy="RESOLVE",
                confidence=None,
                status="resolved",
                summary_text=summary_text[:256],
                entity_ids=created_entity_ids,
                statement_ids=[stmt["statement_id"]],
                trigger_detail={
                    "statement_id": stmt["statement_id"],
                    "statement_text": f"未识别语句：{stmt['statement_text']}",
                },
                solution_detail={
                    "title": "RESOLVE — 指代消解成功",
                    "changes": changes,
                },
                execution_detail=tracker.to_dict(),
            )

        return validated.resolved

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
        failed_count = sum(1 for r in results if isinstance(r, Exception))

        # 记录失败的异常
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"描述合并异常 entity={candidates[i].get('name', '?')}: {r}")

        return {
            "status": "success",
            "candidate_count": len(candidates),
            "merged_count": merged_count,
            "failed_count": failed_count,
        }

    async def _merge_one_entity(self, entity: Dict, end_user_id: str,
                                baseline: str, language: str) -> bool:
        """对单个实体执行描述合并 + 事件提取 + 更名判断"""
        tracker = ExecutionTracker(model=getattr(self.llm_client, "model_name", ""))
        description = entity["description"]
        existing_summary = entity.get("description_summary")
        existing_timeline = entity.get("description_timeline")
        existing_event_timeline = entity.get("event_timeline") or ""

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

        # Step 2: LLM 合并 + 事件提取 + 更名判断
        tracker.start_step("LLM 合并+事件提取+更名", "llm")
        result = await summarize_extract_and_rename(
            llm_client=self.llm_client,
            entity_name=entity["name"],
            entity_type=entity["entity_type"],
            description=description,
            summary=existing_summary,
            event_timeline=existing_event_timeline,
            language=language,
        )

        if not result:
            tracker.end_step("LLM 调用失败", success=False)
            return False

        tracker.end_step(
            f"summary={len(result.description_summary)}字, "
            f"events={len(result.new_events)}, "
            f"rename={result.should_rename_entity}"
        )

        # Step 3: 兜底校验 summary
        tracker.start_step("校验", "decide")
        valid, reason = validate_summary_output(existing_summary, result)
        if not valid:
            tracker.end_step(f"校验失败: {reason}", success=False)
            logger.warning(
                f"描述合并校验失败 entity={entity['name']}, reason={reason}, 跳过写入"
            )
            return False
        tracker.end_step("校验通过")

        # Step 4: 过滤 events
        tracker.start_step("事件过滤", "decide")
        valid_events = filter_events(result.new_events)

        # 构建 event_timeline
        if valid_events:
            events_str = '；'.join(f'[{e.valid_at}|{e.invalid_at}] {e.fact}' for e in valid_events)
            if existing_event_timeline:
                event_timeline = existing_event_timeline + "；" + events_str
            else:
                event_timeline = events_str
        else:
            event_timeline = existing_event_timeline

        tracker.end_step(
            f"有效事件 {len(valid_events)}/{len(result.new_events)}"
        )

        # Step 5: 写入 Neo4j（summary + timeline + event_timeline + 清空 description）
        tracker.start_step("写入", "write")
        merged_text = result.description_summary
        await self.connector.execute_query(
            REFLECTION_DESC_UPDATE,
            entity_id=entity["entity_id"],
            summary=merged_text,
            timeline=timeline,
            event_timeline=event_timeline,
        )
        tracker.end_step("写入完成")

        # Step 6: 更名判断
        if result.should_rename_entity and result.suggested_entity_name:
            await self._try_rename_entity(
                entity=entity,
                suggested_name=result.suggested_entity_name,
                end_user_id=end_user_id,
            )

        # 写 ReflectionLog（每次创建新 session）
        log_repo = self.log_repo_factory()
        log_repo.create(
            end_user_id=end_user_id,
            sub_problem="description_merge",
            trigger_type="scheduled",
            baseline=baseline,
            strategy="MERGE",
            confidence=None,
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

    async def _try_rename_entity(self, entity: Dict, suggested_name: str,
                                 end_user_id: str):
        """尝试更名实体，含兜底校验"""
        old_name = entity["name"]

        # 兜底校验
        if not suggested_name or not suggested_name.strip():
            return
        if suggested_name.strip() == old_name:
            return
        if old_name == "用户" or suggested_name.strip() == "用户":
            return

        # 查重
        conflict_result = await self.connector.execute_query(
            REFLECTION_RENAME_CHECK_CONFLICT,
            end_user_id=end_user_id,
            suggested_name=suggested_name.strip(),
            current_entity_id=entity["entity_id"],
        )
        if conflict_result and conflict_result[0].get("conflict_count", 0) > 0:
            logger.warning(
                f"更名冲突 entity={old_name} -> {suggested_name}, "
                f"end_user_id={end_user_id}"
            )
            return

        # 执行更名
        await self.connector.execute_query(
            REFLECTION_RENAME_ENTITY,
            entity_id=entity["entity_id"],
            new_name=suggested_name.strip(),
            old_name=old_name,
        )

        # 重新生成 name_embedding（同步方法）
        if self.embedding_client:
            try:
                name_embedding = self.embedding_client.embed_query(suggested_name.strip())
                if name_embedding:
                    await self.connector.execute_query(
                        REFLECTION_UPDATE_NAME_EMBEDDING,
                        entity_id=entity["entity_id"],
                        name_embedding=name_embedding,
                    )
            except Exception as emb_err:
                logger.warning(f"更名后补 name_embedding 失败: {emb_err}")

        logger.info(f"实体更名: {old_name} → {suggested_name}, entity_id={entity['entity_id']}")
