"""Layer 2 离线巡检 — 统一编排"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from app.core.utils.datetime_utils import utcnow_naive
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
    apply_event_operations,
    validate_event_operations,
    _parse_timeline_full,
)
from app.core.memory.storage_services.reflection_engine.llm.unresolved_resolver import (
    resolve_unresolved_statement,
    validate_unresolved_output,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.utils.debug.reflection_snapshot_recorder import (
    ReflectionSnapshotRecorder,
    change,
)
from app.repositories.neo4j.cypher_queries import (
    REFLECTION_DESC_UPDATE,
    REFLECTION_RENAME_CHECK_CONFLICT,
    REFLECTION_RENAME_ENTITY,
    REFLECTION_UPDATE_NAME_EMBEDDING,
    UNRESOLVED_CREATE_ENTITY,
    UNRESOLVED_UPDATE_NAME_EMBEDDING,
    UNRESOLVED_APPEND_USER_INFO,
    UNRESOLVED_CREATE_RELATIONSHIP,
    UNRESOLVED_CREATE_STATEMENT_ENTITY_EDGE,
    UNRESOLVED_UPDATE_STATEMENT_FLAG,
)

logger = logging.getLogger(__name__)


class DescriptionMergeConfig(BaseModel):
    """子问题 6 实体描述合并配置"""
    min_fragments: int = 5              # 碎片数阈值（≥此值才触发合并）
    merge_batch_size: int = 15          # 每批最多处理实体数
    merge_concurrency: int = 5          # LLM 并发数


class EntityDedupConfig(BaseModel):
    """子问题 3 去重消歧配置"""
    # === 方案A：高频两路召回 ===
    candidate_cap_name: int = 200       # 路径A最大候选数
    candidate_cap_embed: int = 200      # 路径B最大候选数
    top_k_embed: int = 60              # 向量索引top-K
    theta_embed_floor: float = 0.70     # 向量初筛阈值
    alpha: float = 0.4                  # 名称权重
    beta: float = 0.6                   # 向量权重
    theta_low: float = 0.70             # 丢弃阈值（P≤此值写Redis缓存）
    llm_merge_threshold: float = 0.85   # LLM确认后合并阈值
    max_merges_per_run: int = 10        # 单次最多合并数
    merge_concurrency: int = 5          # LLM并发数
    merge_max_degree: int = 1000        # loser 度数 ≤ 此值原子合并；> 则跳过（超级节点保护，需压测校准）

    # === 方案B：低频分组 LLM ===
    min_entities_for_scan: int = 3      # 少于此数不扫描
    max_pairs_per_run: int = 10         # 单次最多合并对数


class AliasMergeConfig(BaseModel):
    """别名归并 LLM 校验配置"""
    confidence_threshold: float = 0.9   # merge 阈值，confidence >= 此值才合并
    max_per_run: int = 20               # 单次反思最多判定/处理的候选别名数


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
    # 别名归并 LLM 校验
    alias_merge: AliasMergeConfig = AliasMergeConfig()
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
        self._recorder: Optional[ReflectionSnapshotRecorder] = None

    def _snap(self, subproblem: str, stage: str, data) -> None:
        """安全落普通阶段文件；recorder 为 None / 关闭 / 异常时只告警，不影响主流程。"""
        rec = self._recorder
        if rec is None:
            return
        try:
            rec.record_stage(subproblem, stage, data)
        except Exception as e:
            logger.warning(f"[ReflectionSnapshot] {subproblem}/{stage} 落盘失败: {e}")

    def _snap_changes(self, subproblem: str, changes: list) -> None:
        """安全落 3_changes.json。"""
        rec = self._recorder
        if rec is None:
            return
        try:
            rec.record_changes(subproblem, changes)
        except Exception as e:
            logger.warning(f"[ReflectionSnapshot] {subproblem}/3_changes 落盘失败: {e}")

    def _snap_summary(self, results: dict) -> None:
        """安全落 0_summary.json。"""
        rec = self._recorder
        if rec is None:
            return
        try:
            rec.record_summary(results)
        except Exception as e:
            logger.warning(f"[ReflectionSnapshot] 0_summary 落盘失败: {e}")

    async def run(self, end_user_id: str, baseline: str = "HYBRID",
                  language: str = "zh") -> Dict[str, Any]:
        """执行 Layer 2 巡检

        执行顺序按架构设计：1→2→5→3→6→4
        当前已实现子问题 3（去重）和 6（描述合并），其他预留。
        """
        results = {}
        run_t0 = time.perf_counter()
        logger.info(f"[Layer2] 巡检开始 end_user_id={end_user_id}, baseline={baseline}")

        # 反思快照（高频）：开关关闭时 recorder 内部 no-op，零开销
        self._recorder = ReflectionSnapshotRecorder(
            end_user_id=end_user_id,
            scan_type="layer2_frequent",
            baseline=baseline,
        )
        try:
            # TODO: 子问题 1 — 过期检测（stale_detection）
            # TODO: 子问题 2 — 事实矛盾检测（fact_contradiction）

            # 未识别实体处理：把"未识别实体"语句解析成正式实体/关系并入图
            unresolved = await self._run_unresolved_resolver(
                end_user_id, baseline, language
            )
            results["unresolved_entity"] = unresolved
            logger.info(
                f"[Layer2 高频] 未识别实体处理完成 end_user_id={end_user_id}, "
                f"候选={unresolved.get('total', 0)}, 解析={unresolved.get('resolved', 0)}, "
                f"强制入库={unresolved.get('forced', 0)}, 失败={unresolved.get('failed', 0)}"
            )

            # 别名归并：LLM 校验后按 merge/drop 处理 "别名属于" 关系
            # 放在 unresolved 之后、entity_dedup 之前：先清理别名节点
            alias = await self._run_alias_merge(end_user_id, baseline, language)
            results["alias_merge"] = alias
            logger.info(
                f"[Layer2 高频] 别名归并完成 end_user_id={end_user_id}, "
                f"合并={alias.get('merge_count', 0)}, 丢弃={alias.get('drop_count', 0)}, "
                f"边重定向={alias.get('edges_redirected', 0)}, 节点删除={alias.get('alias_nodes_deleted', 0)}, "
                f"PG同步={alias.get('pg_synced', False)}"
            )

            # 复杂去重消歧：两路召回候选 + LLM 判定后合并重复实体
            dedup = await self._run_entity_dedup(end_user_id, baseline)
            results["entity_dedup"] = dedup
            logger.info(
                f"[Layer2 高频] 实体去重完成 end_user_id={end_user_id}, "
                f"候选={dedup.get('candidate_count', 0)}, LLM判定={dedup.get('llm_pool', 0)}, "
                f"合并={dedup.get('merged_count', 0)}, 记录未合并={dedup.get('recorded_count', 0)}"
            )

            # 子问题 7 — 用户实体元数据提取
            # 放在 description_merge 之前：metadata 提取的输入是 description 原始碎片，
            # description_merge 会清空 description 并写入 description_summary
            meta = await self._run_metadata_extraction(
                end_user_id, language
            )
            results["metadata_extraction"] = meta
            logger.info(
                f"[Layer2 高频] 元数据提取完成 end_user_id={end_user_id}, "
                f"提取={meta.get('extracted', 0)}, 失败={meta.get('failed', 0)}"
            )

            # 描述合并：把同一实体的多条描述合并、必要时更名
            desc = await self._run_description_merge(
                end_user_id, baseline, language
            )
            results["description_merge"] = desc
            logger.info(
                f"[Layer2 高频] 描述合并完成 end_user_id={end_user_id}, "
                f"候选={desc.get('candidate_count', 0)}, 合并={desc.get('merged_count', 0)}, "
                f"失败={desc.get('failed_count', 0)}"
            )

            # TODO: 子问题 4 — 本体 Metadata 校验（metadata_validation）

            logger.info(
                f"[Layer2 高频] 巡检结束 end_user_id={end_user_id}, "
                f"耗时={time.perf_counter() - run_t0:.2f}s"
            )
            return results
        finally:
            # finally 保证软超时/异常下 summary 不丢；空转轮由 recorder 内部跳过、整轮零文件。
            self._snap_summary(results)

    async def _run_alias_merge(self, end_user_id: str, baseline: str = "HYBRID",
                               language: str = "zh") -> Dict[str, Any]:
        """别名归并：LLM 校验后按 merge/drop 处理 "别名属于" 关系。

        S0 收集候选 → S1 LLM 分组判定（merge/drop/skip）→ S2 删 drop 边 →
        S3-5 按 merge 集合归并 → S6 PG 同步（在 merge_alias_belongs_to 内）→ 写日志。
        判定失败的候选 skip（保留边，下次重判）；删边只在明确低置信时发生。
        """
        from collections import defaultdict
        from .deterministic.alias_merger import (
            merge_alias_belongs_to, collect_alias_candidates, drop_alias_edges,
        )
        from .llm.alias_belongs_judge import judge_alias_belongs

        cfg = self.config.alias_merge
        try:
            t0 = time.perf_counter()
            candidates = await collect_alias_candidates(self.connector, end_user_id)
            recall_ms = int((time.perf_counter() - t0) * 1000)
            if not candidates:
                # 无候选，仅做 PG 同步（幂等）
                return await merge_alias_belongs_to(self.connector, end_user_id, alias_ids=[])

            # 每轮限流（处理后候选会减少，超出部分留待下次）
            candidates = candidates[:cfg.max_per_run]
            # 快照：1_input（GET_ALIAS_BELONGS_CANDIDATES 原始候选行，含 alias_*/target_*）
            self._snap("alias_merge", "1_input", {"candidates": candidates})

            # 按 target 分组（一个 canonical 一组判定）
            groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for c in candidates:
                groups[c["target_id"]].append(c)

            merge_ids: List[str] = []
            drop_ids: List[str] = []
            decided_log: List[tuple] = []   # (canonical, cand, decided) 仅 LLM 判定过的，用于写日志
            snap_skipped: List[Dict[str, Any]] = []   # LLM 未给有效判定的候选（快照 skipped）

            llm_t0 = time.perf_counter()
            for target_id, group in groups.items():
                head = group[0]
                canonical = {
                    "name": head["target_name"],
                    "entity_type": head["target_entity_type"],
                    "description": head["target_description"],
                    "description_summary": head["target_description_summary"],
                    "aliases": head["target_aliases"] or [],
                }
                existing_lower = {a.strip().lower() for a in canonical["aliases"] if a and a.strip()}

                to_judge: List[Dict[str, Any]] = []
                for c in group:
                    # 重复短路：候选名已在 target.aliases（忽略大小写与前后空白）→ 直接 merge，
                    # 不送 LLM，但写反思日志表
                    if (c["alias_name"] or "").strip().lower() in existing_lower:
                        merge_ids.append(c["alias_id"])
                        decided_log.append((canonical, c, {
                            "alias_id": c["alias_id"],
                            "decision": "merge",
                            "confidence": 1.0,
                            "reason": "已是现有别名，直接归并",
                            "shortcut": True,
                        }))
                    else:
                        to_judge.append(c)

                if not to_judge:
                    continue

                decided = await judge_alias_belongs(
                    self.llm_client, canonical, to_judge,
                    threshold=cfg.confidence_threshold, language=language,
                )
                decided_by_id = {d["alias_id"]: d for d in decided}
                for c in to_judge:
                    d = decided_by_id.get(c["alias_id"])
                    if d is None:
                        snap_skipped.append(c)  # skip：保留边，下次重判
                        continue
                    (merge_ids if d["decision"] == "merge" else drop_ids).append(c["alias_id"])
                    decided_log.append((canonical, c, d))
            llm_ms = int((time.perf_counter() - llm_t0) * 1000)

            # S2 drop（只删边）
            await drop_alias_edges(self.connector, end_user_id, drop_ids)
            # S3-5 merge（按 merge 集合）+ S6 PG 同步
            result = await merge_alias_belongs_to(self.connector, end_user_id, alias_ids=merge_ids)

            # 写日志（含 LLM 判定与重复短路命中的候选；仅 skip 不写）
            timing = {"recall_ms": recall_ms, "llm_ms": llm_ms}
            for canonical, cand, d in decided_log:
                self._write_alias_log(end_user_id, canonical, cand, d, timing, baseline)

            # 快照：2_llm_raw（判定结果）+ 3_changes（merge/drop/sync_pg/skipped）
            self._snap("alias_merge", "2_llm_raw", {
                "threshold": cfg.confidence_threshold,
                "decided": [
                    {"alias_id": cand["alias_id"], "alias_name": cand["alias_name"],
                     "decision": d["decision"], "confidence": d["confidence"],
                     "reason": d["reason"], "shortcut": bool(d.get("shortcut"))}
                    for (_canon, cand, d) in decided_log
                ],
                "short_circuit": [
                    cand["alias_id"] for (_canon, cand, d) in decided_log if d.get("shortcut")
                ],
            })
            snap_changes: list = []
            for _canon, cand, d in decided_log:
                if d["decision"] == "merge":
                    # merge 变更对象是 target(规范)实体；aliases/description 增量按
                    # MERGE_ALIAS_BELONGS_TO 语义推导（由候选快照推导，不读写后回值）
                    old_aliases = list(cand.get("target_aliases") or [])
                    new_aliases = old_aliases + (
                        [cand["alias_name"]] if cand["alias_name"] not in old_aliases else [])
                    fc = []
                    # 别名已在 target.aliases（短路命中）时 aliases 无实际新增，不记 old==new；
                    # 此时 merge 的真实效果是删别名节点 + 重定向边，由 action=merge + extra 体现
                    if new_aliases != old_aliases:
                        fc.append({"field": "aliases", "old": ", ".join(old_aliases),
                                   "new": ", ".join(new_aliases)})
                    old_desc = cand.get("target_description") or ""
                    alias_desc = cand.get("alias_description") or ""
                    if alias_desc and alias_desc not in old_desc:
                        new_desc = alias_desc if not old_desc else f"{old_desc}；{alias_desc}"
                        fc.append({"field": "description", "old": old_desc, "new": new_desc})
                    snap_changes.append(change(
                        "entity", "merge", target_id=cand["target_id"],
                        target_name=cand["target_name"], field_changes=fc,
                        extra={"alias_id": cand["alias_id"], "confidence": d["confidence"],
                               "source": "short_circuit" if d.get("shortcut") else "llm"},
                    ))
                else:  # drop：只删「别名属于」边、保留节点
                    snap_changes.append(change(
                        "edge", "delete", target_id=cand["alias_id"],
                        target_name=cand["alias_name"],
                        field_changes=[{"field": "别名属于边", "old": "存在", "new": None}],
                        reason="low_confidence",
                        extra={"confidence": d["confidence"], "node_kept": True},
                    ))
            for c in snap_skipped:
                snap_changes.append(change(
                    "edge", "merge", target_id=c["alias_id"], target_name=c["alias_name"],
                    status="skipped", reason="llm_invalid",
                ))
            for step, msg in (result.get("errors") or {}).items():
                snap_changes.append(change(
                    "edge", "redirect_edge" if step == "redirect" else step,
                    status="skipped", reason=f"error:{msg}",
                ))
            snap_changes.append(change(
                "metadata_field", "sync_pg", target_id=end_user_id,
                status="applied" if result.get("pg_synced") else "skipped",
                extra={"synced_fields": ["aliases"]},
            ))
            self._snap_changes("alias_merge", snap_changes)

            result["merge_count"] = len(merge_ids)
            result["drop_count"] = len(drop_ids)
            return result
        except Exception as e:
            logger.warning(f"[AliasMerge] 执行失败 end_user_id={end_user_id}: {e}")
            return {"status": "error", "error": str(e)}

    async def _run_metadata_extraction(self, end_user_id: str,
                                       language: str = "zh") -> Dict[str, Any]:
        """子问题 7：用户实体元数据提取。

        从 Neo4j 中读取当前用户的 User 实体及其 description，
        调用 MetadataExtractionStep 进行 LLM 结构化提取，
        将 patch operations 回写 Neo4j 并同步 PostgreSQL。

        门控：description 碎片数 >= min_fragments（默认 5）才触发提取，
        与 description_merge 共用同一阈值配置。
        放在 entity_dedup 之后、description_merge 之前执行：
        metadata 提取需要 description 原始碎片，而 description_merge 会将其清空。
        """
        try:
            from app.core.memory.storage_services.reflection_engine.deterministic.extract_metadata_service import (
                extract_metadata_for_user,
            )

            want_trace = self._recorder is not None and self._recorder.enabled
            result = await extract_metadata_for_user(
                connector=self.connector,
                llm_client=self.llm_client,
                end_user_id=end_user_id,
                language=language,
                min_fragments=self.desc_config.min_fragments,
                collect_trace=want_trace,
            )

            # 输出每个实体的详细变更日志
            _OP_FMT = {
                "add": ("新增", lambda o: f'{o["field"]}:「{o["value"]}」'),
                "delete": ("删除", lambda o: f'{o["field"]}:「{o["value"]}」'),
                "update": ("更新", lambda o: f'{o["field"]}:「{o["old"]}」→「{o["new"]}」'),
            }
            for detail in result.get("details", []):
                name = detail["entity_name"]
                ops = detail.get("ops", [])
                for op_type, (label, fmt) in _OP_FMT.items():
                    group = [o for o in ops if o["op"] == op_type]
                    if group:
                        logger.info(f"[Metadata] {name} {label}({len(group)}): "
                                    + "; ".join(fmt(o) for o in group))

            trace = result.pop("_trace", None)  # 快照数据从业务返回值剥离
            if trace is not None:
                self._snap("metadata_extraction", "1_input", trace.get("input"))
                self._snap("metadata_extraction", "2_llm_raw", trace.get("llm_raw"))
                self._snap_changes("metadata_extraction", trace.get("changes") or [])
            return {"status": "success", **result}
        except Exception as e:
            logger.warning(
                f"[Metadata] 元数据提取失败 end_user_id={end_user_id}: {e}"
            )
            return {"status": "error", "error": str(e)}

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
        # 记录候选总数（用于返回值；直合池剥离前的总和）
        total_candidates = len(candidates)

        # 快照：仅在有候选时落盘，空轮（无任何候选）不产生空文件。
        # DedupCandidatePair 无原始向量（相似度已是标量），无需截断。
        snap_on = total_candidates > 0
        snap_changes: list = []
        if snap_on:
            self._snap("entity_dedup", "1_input", {
                "candidates": [
                    {"a_id": c.a_id, "a_name": c.a_name, "a_aliases": c.a_aliases or [],
                     "b_id": c.b_id, "b_name": c.b_name, "b_aliases": c.b_aliases or [],
                     "entity_type": c.entity_type, "sim_name": c.sim_name,
                     "sim_embed": c.sim_embed, "probability": c.probability,
                     "source_paths": c.source_paths}
                    for c in candidates
                ],
            })

        # 3.5 同名同类型直合池：name 完全相同（归一化后）→ 不进 LLM，确定性合并
        # entity_type 在候选对里两侧本就相同（召回 Cypher 已约束），故只需判 name。
        merged_count = 0
        direct_merged_count = 0
        removed_ids: set = set()  # 直合中被删除的 loser id，用于过滤后续 LLM 池
        # id -> {description, aliases}：直合过程中 keeper 的累积态。
        # 同一 keeper 出现在多对里时，前次合并已经把 loser 的描述/别名累加进 DB，
        # 但本对 pair 里仍是召回快照值。用这里的覆盖确保下一对日志 old 用累积态、
        # 同时 build_merged_aliases 也能按累积态去重并入。
        keeper_state_overrides: Dict[str, Dict[str, Any]] = {}

        def _norm(s: str) -> str:
            return (s or "").strip().lower()

        direct_pairs, rest = [], []
        for c in candidates:
            if c.a_name and _norm(c.a_name) == _norm(c.b_name):
                direct_pairs.append(c)
            else:
                rest.append(c)

        for c in direct_pairs:
            if merged_count >= config.max_merges_per_run:
                break
            ok, removed_id, keeper_id, keeper_desc, keeper_aliases = (
                await self._apply_direct_merge(c, end_user_id, baseline,
                                               keeper_state_overrides)
            )
            if ok:
                merged_count += 1
                direct_merged_count += 1
                if removed_id:
                    removed_ids.add(removed_id)
                # 快照：同名同类型直合（确定性，不经 LLM）
                snap_changes.append(change(
                    "entity", "merge", target_id=keeper_id, target_name=c.a_name,
                    extra={"loser_id": removed_id, "source": "deterministic",
                           "entity_type": c.entity_type},
                ))
                # 把 keeper 的累积态(合并后真实状态)写回 overrides,
                # 让下一对涉及同一 keeper 的合并能看到真实累积,日志 old/new 才能正确反映本次增量
                if keeper_id is not None:
                    keeper_state_overrides[keeper_id] = {
                        "description": keeper_desc or "",
                        "aliases": list(keeper_aliases or []),
                    }

        # 过滤掉涉及已被直合删除的实体的候选对（避免后续 LLM 调用浪费 + 合并失败）
        candidates = [
            c for c in rest
            if c.a_id not in removed_ids and c.b_id not in removed_ids
        ]

        # 4. 两档分流（去掉自动合并，全部走 LLM）
        llm_pool, discard_pool = partition_by_probability(
            candidates, config.theta_low)
        await cache_discarded(end_user_id, discard_pool)

        # 快照：低分丢弃池（filtered）
        for p in discard_pool:
            snap_changes.append(change(
                "entity", "merge", target_id=p.a_id, target_name=p.a_name,
                status="filtered", reason="low_probability",
                extra={"loser_id": p.b_id, "probability": p.probability},
            ))

        # 5. LLM 判定（所有候选均需 LLM 确认）— 计时
        t0 = time.perf_counter()
        llm_results = await judge_batch(
            self.llm_client, llm_pool,
            config.merge_concurrency,
        )
        llm_ms = int((time.perf_counter() - t0) * 1000)

        # 快照：LLM 池 + 逐对裁决（pair 用名字便于阅读，另带 id 定位）
        if snap_on:
            self._snap("entity_dedup", "2_llm_raw", {
                "llm_pool": [f"{p.a_name} ↔ {p.b_name}" for p in llm_pool],
                "decisions": [
                    {"pair": f"{pair.a_name} ↔ {pair.b_name}",
                     "a_id": pair.a_id, "b_id": pair.b_id,
                     "same_entity": bool(d and d.same_entity),
                     "confidence": (d.confidence if d else 0.0),
                     "winner_id": (d.winner_id if d else "a"),
                     "merged_name": (d.merged_name if d else ""),
                     "new_aliases": (d.new_aliases if d else []),
                     "reason": (d.reason if d else "")}
                    for pair, d in llm_results
                ],
            })

        # 均摊耗时（每对候选分摊批量耗时）
        n = max(len(llm_pool), 1)
        step_timing = {
            "recall_ms": recall_ms // n,
            "score_ms": score_ms // n,
            "llm_ms": llm_ms // n,
        }

        # 6. 合并执行（全部经 LLM 确认后才合并）
        # merged_count 已在 3.5 直合池处初始化，与直合共享上限
        recorded_count = 0
        rejected_pairs = []
        for pair, decision in llm_results:
            if merged_count >= config.max_merges_per_run:
                break
            if decision and decision.same_entity and decision.confidence >= config.llm_merge_threshold:
                success = await self._apply_dedup_merge(pair, end_user_id, baseline, llm_decision=decision, step_timing=step_timing)
                if success:
                    merged_count += 1
                    # 快照：LLM 确认合并
                    snap_changes.append(change(
                        "entity", "merge", target_id=pair.a_id, target_name=pair.a_name,
                        extra={"loser_id": pair.b_id, "confidence": decision.confidence,
                               "source": "llm"},
                    ))
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
                # 快照：LLM 拒绝（NO_OP）
                snap_changes.append(change(
                    "entity", "merge", target_id=pair.a_id, target_name=pair.a_name,
                    status="rejected", reason="llm_no_op",
                    extra={"loser_id": pair.b_id, "confidence": conf},
                ))

        # 批量写入丢弃缓存（一次 Redis 往返）
        if rejected_pairs:
            await cache_discarded(end_user_id, rejected_pairs)

        # 快照：落 3_changes（空轮 snap_on=False 时不写）
        if snap_on:
            self._snap_changes("entity_dedup", snap_changes)

        return {
            "status": "success",
            "candidate_count": total_candidates,
            "llm_pool": len(llm_pool),
            "discard_pool": len(discard_pool),
            "merged_count": merged_count,
            "direct_merged_count": direct_merged_count,
            "recorded_count": recorded_count,
        }


    async def run_dedup_full_scan(self, end_user_id: str, baseline: str = "HYBRID") -> Dict[str, Any]:
        """子问题 3 复杂去重 方案B：低频全量扫描去重（公共入口）"""
        t0 = time.perf_counter()
        logger.info(f"[Layer2 低频] 全量去重开始 end_user_id={end_user_id}")
        # 反思快照（低频）：只有 entity_dedup 一个子问题
        self._recorder = ReflectionSnapshotRecorder(
            end_user_id=end_user_id,
            scan_type="dedup_full_scan",
            baseline=baseline,
        )
        result: Dict[str, Any] = {}
        try:
            result = await self._run_dedup_full_scan(end_user_id, baseline=baseline)
            logger.info(
                f"[Layer2 低频] 全量去重完成 end_user_id={end_user_id}, "
                f"扫描类型={result.get('scanned_types', 0)}, "
                f"合并={result.get('merged_count', 0)}, "
                f"耗时={time.perf_counter() - t0:.2f}s"
            )
            return result
        finally:
            self._snap_summary({"entity_dedup": result})

    async def _run_dedup_full_scan(self, end_user_id: str, baseline: str = "HYBRID") -> Dict[str, Any]:
        """子问题 3 复杂去重 方案B：低频全量扫描去重"""
        from .deterministic.full_scan_dedup import (
            get_entity_types, get_last_scan_time, check_new_entities,
            fetch_entities_by_type, update_scan_time,
        )
        from .llm.entity_dedup_batch_judge import judge_batch_dedup
        from .deterministic.cypher_merger import (
            choose_keeper, execute_merge, build_merged_aliases,
            # fetch_degrees,  # 暂时关闭度数优先,下版打开
        )

        config = self.dedup_config
        total_merged = 0
        total_direct_merged = 0
        scanned_types = 0
        # 快照累积（跨类型聚合到 entity_dedup 一个子目录）
        snap_input: list = []
        snap_llm: list = []
        snap_changes: list = []

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

            # 快照：1_input（按类型分组的实体）
            snap_input.append({
                "scanned_type": entity_type,
                "entity_count": len(entities),
                "entities": [
                    {"entity_id": e.get("entity_id"), "name": e.get("name"),
                     "entity_type": e.get("entity_type"), "description": e.get("description"),
                     "aliases": e.get("aliases") or []}
                    for e in entities
                ],
            })

            # 同名同类型直合（确定性快路）：分组内 name 完全相同 → 直接合并不进 LLM
            # 受 max_pairs_per_run 限制；被合并的实体从 entities 剔除，避免送 LLM 浪费
            direct_merged_count, removed_ids = await self._direct_merge_in_group(
                entities, entity_type, end_user_id, baseline,
                max_merges=config.max_pairs_per_run,
                snap_sink=snap_changes,
            )
            total_merged += direct_merged_count
            total_direct_merged += direct_merged_count
            if removed_ids:
                entities = [e for e in entities if e["entity_id"] not in removed_ids]
            # LLM 分组判定 — 计时
            t0 = time.perf_counter()
            pairs = await judge_batch_dedup(self.llm_client, entities, entity_type)
            llm_ms = int((time.perf_counter() - t0) * 1000)
            llm_ms_per_pair = llm_ms // max(len(pairs), 1)

            # 快照：2_llm_raw（judge_batch_dedup 元组裁决，idx 映射成名字便于阅读）
            def _ename(idx):
                return entities[idx]["name"] if 0 <= idx < len(entities) else None

            def _eid(idx):
                return entities[idx]["entity_id"] if 0 <= idx < len(entities) else None

            snap_llm.append({
                "scanned_type": entity_type,
                "pairs": [
                    {"pair": f"{_ename(ia)} ↔ {_ename(ib)}",
                     "a_id": _eid(ia), "b_id": _eid(ib), "idx_a": ia, "idx_b": ib,
                     "confidence": cf, "new_name": nn, "new_aliases": na, "reason": rs}
                    for (ia, ib, cf, rs, nn, na) in pairs
                ],
            })

            merged_count = 0
            for idx_a, idx_b, conf, reason, new_name, new_aliases in pairs:
                # 单类型 LLM 合并数 + 直合数共享 max_pairs_per_run 上限
                if (merged_count + direct_merged_count) >= config.max_pairs_per_run:
                    break
                if idx_a == idx_b:
                    continue  # 跳过无效对（同一实体）

                ea, eb = entities[idx_a], entities[idx_b]
                if ea["entity_id"] == eb["entity_id"]:
                    continue  # 跳过同 ID 实体

                # confidence 阈值检查（和方案A一致）
                if conf < config.llm_merge_threshold:
                    continue

                degree_a, degree_b = 0, 0
                # === 度数优先（暂时关闭，下版打开）===
                # degree_a, degree_b = await fetch_degrees(
                #     self.connector, end_user_id, ea["entity_id"], eb["entity_id"])
                # =====================================
                keeper, loser = choose_keeper(ea, eb, None, degree_a, degree_b)
                loser_degree = degree_b if loser is eb else degree_a
                merged_name = new_name or keeper["name"]
                merged_aliases = build_merged_aliases(keeper, loser, merged_name, new_aliases)

                t1 = time.perf_counter()
                merge_status = await execute_merge(
                    self.connector, end_user_id,
                    keeper["entity_id"], loser["entity_id"],
                    merged_name, merged_aliases,
                    loser_degree=loser_degree,
                    merge_max_degree=config.merge_max_degree,
                )
                write_ms = int((time.perf_counter() - t1) * 1000)

                if merge_status == "success":
                    merged_count += 1
                    await self._reembed_if_name_changed(keeper, merged_name)
                    # 快照：LLM 确认合并。与高频 entity_dedup 一致：field_changes 留空，
                    # 关键信息走 extra.loser_id；merged 别名详情在 2_llm_raw 的 new_aliases 已有
                    snap_changes.append(change(
                        "entity", "merge", target_id=keeper["entity_id"],
                        target_name=merged_name,
                        extra={"loser_id": loser["entity_id"], "confidence": conf,
                               "source": "llm", "entity_type": entity_type},
                    ))
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

        # 快照：扫描到类型才落盘（空轮不产生文件）
        if scanned_types > 0:
            self._snap("entity_dedup", "1_input", {"types": snap_input})
            self._snap("entity_dedup", "2_llm_raw", {"types": snap_llm})
            self._snap_changes("entity_dedup", snap_changes)

        return {
            "scanned_types": scanned_types,
            "merged_count": total_merged,
            "direct_merged_count": total_direct_merged,
        }
    @staticmethod
    def _merged_description(keeper_desc: str, loser_desc: str) -> str:
        """与 Cypher DEDUP_MERGE_ENTITIES 一致的 description 拼接策略：
        任一为空取另一边，两边都有用 '；' 拼。日志和本地回填共用。
        """
        kd = keeper_desc or ""
        ld = loser_desc or ""
        if not kd:
            return ld
        if not ld:
            return kd
        return f"{kd}；{ld}"

    def _write_dedup_log(self, end_user_id: str, keeper: Dict, loser: Dict,
                         entity_type: str, merged_name: str, merged_aliases: List,
                         confidence: float, execution_detail: Dict, reason: str = "",
                         status: str = "resolved", strategy: str = "MERGE",
                         baseline: str = "HYBRID", source: str = "llm"):
        """写去重 ReflectionLog（方案A和B共用，支持 resolved/recorded）

        Args:
            source: 合并来源；"llm"=LLM 确认合并，"deterministic"=同名同类型确定性合并。
                title 显示按此区分，不依赖 reason 字符串硬编码。
        """
        if status == "resolved":
            merged_desc = self._merged_description(
                keeper.get("description", ""), loser.get("description", ""))
            changes = [c for c in [
                {"field": "name", "old": keeper["name"], "new": merged_name},
                {"field": "aliases",
                 "old": ", ".join(sorted(keeper.get("aliases") or [])),
                 "new": ", ".join(sorted(merged_aliases))},
                {"field": "description",
                 "old": keeper.get("description", ""),
                 "new": merged_desc},
            ] if c["old"] != c["new"]]
            summary = f'"{keeper["name"]}" ≈ "{loser["name"]}" → 合并'
            if source == "deterministic":
                title = "MERGE — 同名同类型确定性合并"
            else:
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
        
    def _write_alias_log(self, end_user_id: str, canonical: Dict[str, Any],
                         cand: Dict[str, Any], decided: Dict[str, Any],
                         timing: Dict[str, Any], baseline: str = "HYBRID"):
        """写别名归并 ReflectionLog（merge / drop 各一条）。"""
        is_merge = decided["decision"] == "merge"
        confidence = decided["confidence"]
        reason = decided["reason"]
        shortcut = decided.get("shortcut", False)
        alias_name = (cand.get("alias_name") or "").strip()

        tracker = ExecutionTracker(model=getattr(self.llm_client, "model_name", ""))
        tracker.steps.append(ExecutionStep(
            name="候选收集", type="prompt", duration_ms=timing.get("recall_ms") or 0,
            output=f"alias={alias_name}", success=True,
        ))
        if shortcut:
            tracker.steps.append(ExecutionStep(
                name="规则判定", type="decide", duration_ms=0,
                output="候选名已是现有别名，直接归并", success=True,
            ))
        else:
            tracker.steps.append(ExecutionStep(
                name="LLM 判定", type="llm", duration_ms=timing.get("llm_ms") or 0,
                output=f"decision={decided['decision']}, confidence={confidence:.2f}", success=True,
            ))
        tracker.steps.append(ExecutionStep(
            name="归并" if is_merge else "删边", type="write",
            duration_ms=0,
            output=(f'已并入 "{canonical["name"]}" 的 aliases' if is_merge else "已删除「别名属于」边"),
            success=True,
        ))

        # 有效描述：description_summary 优先，回退 description
        canon_desc = (canonical.get("description_summary") or "").strip() or (canonical.get("description") or "")
        alias_desc = (cand.get("alias_description_summary") or "").strip() or (cand.get("alias_description") or "")
        canon_aliases = canonical.get("aliases") or []

        entity_a = {"entity_id": cand["target_id"], "name": canonical["name"],
                    "entity_type": canonical.get("entity_type"),
                    "description": canon_desc, "aliases": canon_aliases}
        entity_b = {"entity_id": cand["alias_id"], "name": alias_name,
                    "entity_type": cand.get("alias_entity_type"),
                    "description": alias_desc, "aliases": cand.get("alias_aliases") or []}
        trigger = {"entity_a": entity_a, "entity_b": entity_b, "reason": reason[:200]}

        if is_merge:
            already = alias_name.lower() in {a.strip().lower() for a in canon_aliases if a and a.strip()}
            summary = f'"{alias_name}" → "{canonical["name"]}" 合并别名'
            title = "MERGE — 已是现有别名，直接归并" if shortcut else "MERGE — LLM确认别名归并"
            # 别名已存在（无实际新增）时不产出 diff，避免前端展示 old==new 的无意义对比
            changes = [] if already else [{
                "field": "aliases",
                "old": ", ".join(canon_aliases),
                "new": ", ".join(canon_aliases + [alias_name]),
            }]
            strategy = "MERGE"
        else:
            summary = f'"{alias_name}" ✗ 判定非别名 → 丢弃别名属于边'
            title = "DROP — LLM判定非别名"
            changes = [{
                "field": "别名属于关系",
                "old": f'{alias_name} →(别名属于) {canonical["name"]}',
                "new": "关系已删除（非本人别名）",
            }]
            strategy = "DROP"

        log_repo = self.log_repo_factory()
        log_repo.create(
            end_user_id=end_user_id,
            sub_problem="alias_merge",
            trigger_type="scheduled",
            baseline=baseline,
            strategy=strategy,
            confidence=confidence,
            status="resolved",
            summary_text=summary[:256],
            entity_ids=[cand["target_id"], cand["alias_id"]],
            trigger_detail=trigger,
            solution_detail={"title": title, "changes": changes},
            execution_detail=tracker.to_dict(),
        )

    async def _apply_dedup_merge(self, pair, end_user_id: str, baseline: str,
                                llm_decision=None, step_timing=None) -> bool:
        """执行单对合并 + 写 ReflectionLog"""
        from .deterministic.cypher_merger import (
            choose_keeper, execute_merge, build_merged_aliases,
            # fetch_degrees,  # 暂时关闭度数优先,下版打开
        )

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

        # 度数查询：用于 keeper 选择 + 超级节点保护
        degree_a, degree_b = 0, 0
        # === 度数优先（暂时关闭，下版打开）===
        # degree_a, degree_b = await fetch_degrees(
        #     self.connector, end_user_id, pair.a_id, pair.b_id)
        # =====================================
        keeper, loser = choose_keeper(entity_a, entity_b, winner, degree_a, degree_b)
        loser_degree = degree_b if loser is entity_b else degree_a

        merged_name = llm_decision.merged_name if llm_decision and llm_decision.merged_name else keeper["name"]
        new_aliases = llm_decision.new_aliases if llm_decision else None
        merged_aliases = build_merged_aliases(keeper, loser, merged_name, new_aliases)
        tracker.end_step(f"keeper={keeper['name']}")

        # Step 5: 写入
        tracker.start_step("写入", "write")
        merge_status = await execute_merge(
            self.connector, end_user_id,
            keeper["entity_id"], loser["entity_id"],
            merged_name, merged_aliases,
            loser_degree=loser_degree,
            merge_max_degree=self.dedup_config.merge_max_degree,
        )
        if merge_status == "skipped_super_node":
            # 当前版本超级节点保护已关闭，理论上不会进此分支；保留兜底。
            tracker.end_step("跳过超级节点", success=False)
            return False
        if merge_status != "success":
            tracker.end_step("合并失败", success=False)
            return False
        tracker.end_step("合并完成")

        # Step 5.5: name 变更则重算 name_embedding
        await self._reembed_if_name_changed(keeper, merged_name)

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

    async def _reembed_if_name_changed(self, keeper: Dict, merged_name: str) -> None:
        """合并后若 name 变化，重算 name_embedding（与更名流程一致；失败只告警不回滚）"""
        old_name = keeper.get("name") or ""
        if not merged_name or merged_name == old_name:
            return
        if not self.embedding_client:
            return
        try:
            emb = self.embedding_client.embed_query(merged_name)
            if emb:
                await self.connector.execute_query(
                    REFLECTION_UPDATE_NAME_EMBEDDING,
                    entity_id=keeper.get("entity_id") or keeper.get("id"),
                    name_embedding=emb,
                )
        except Exception as e:
            logger.warning(f"合并后重算 name_embedding 失败 name={merged_name}: {e}")

    async def _apply_direct_merge(
        self, pair, end_user_id: str, baseline: str,
        keeper_state_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str], Optional[List[str]]]:
        """同名同类型确定性合并（不经 LLM），写 ReflectionLog

        Args:
            keeper_state_overrides: 可选 {entity_id: {description, aliases}}，
                同一直合循环内若 keeper 已经被前次合并累积过，从这里取覆盖值，
                而非 pair 上的召回快照值。这样日志 old/new 与 build_merged_aliases
                都能基于真实累积态。

        Returns:
            (是否合并成功, 被删除的 loser_id,
             keeper_id, keeper 合并后的 description, keeper 合并后的 aliases)。
            失败时返回 (False, None, None, None, None)。
            调用方据 loser_id 过滤后续 LLM 池中涉及该实体的候选对，避免无效调用。
            keeper_id/desc/aliases 用于回填 overrides，让下一对涉及同 keeper 的合并
            能拿到累积态。
        """
        from .deterministic.cypher_merger import (
            choose_keeper, execute_merge, build_merged_aliases,
            # fetch_degrees,  # 暂时关闭度数优先,下版打开
        )

        entity_a = {"entity_id": pair.a_id, "name": pair.a_name, "entity_type": pair.entity_type,
                    "description": pair.a_desc, "aliases": pair.a_aliases}
        entity_b = {"entity_id": pair.b_id, "name": pair.b_name, "entity_type": pair.entity_type,
                    "description": pair.b_desc, "aliases": pair.b_aliases}

        # 若该 entity 在本次直合循环里已是某次合并的 keeper，用累积态覆盖召回快照值，
        # 让日志 old 和 build_merged_aliases 都基于真实累积。
        if keeper_state_overrides:
            for ent in (entity_a, entity_b):
                ov = keeper_state_overrides.get(ent["entity_id"])
                if ov:
                    ent["description"] = ov.get("description", ent.get("description", ""))
                    ent["aliases"] = list(ov.get("aliases", ent.get("aliases", []) or []))

        degree_a, degree_b = 0, 0
        # === 度数优先（暂时关闭，下版打开）===
        # degree_a, degree_b = await fetch_degrees(
        #     self.connector, end_user_id, pair.a_id, pair.b_id)
        # =====================================
        keeper, loser = choose_keeper(entity_a, entity_b, None, degree_a, degree_b)
        loser_degree = degree_b if loser is entity_b else degree_a
        merged_name = keeper["name"]
        merged_aliases = build_merged_aliases(keeper, loser, merged_name)  # 不传 new_aliases

        merge_status = await execute_merge(
            self.connector, end_user_id,
            keeper["entity_id"], loser["entity_id"],
            merged_name, merged_aliases,
            loser_degree=loser_degree,
            merge_max_degree=self.dedup_config.merge_max_degree,
        )
        if merge_status != "success":
            return False, None, None, None, None

        # name 同名不变，无需重算 embedding
        self._write_dedup_log(
            end_user_id=end_user_id,
            keeper=keeper, loser=loser,
            entity_type=pair.entity_type,
            merged_name=merged_name,
            merged_aliases=merged_aliases,
            confidence=1.0,
            execution_detail={
                "steps": [
                    {"name": "同名同类型匹配", "type": "decide", "duration_ms": 0,
                     "output": f'name="{merged_name}" type={pair.entity_type}', "success": True},
                    {"name": "写入", "type": "write", "duration_ms": 0,
                     "output": "合并完成", "success": True},
                ],
                "total_ms": 0,
                "model": "",
            },
            reason="同名同类型确定性合并",
            baseline=baseline,
            strategy="MERGE",
            status="resolved",
            source="deterministic",
        )
        # 返回 keeper 合并后的累积态(与 Cypher DEDUP_MERGE_ENTITIES 一致)，
        # 供调用方写入 overrides，让下一对涉及同 keeper 的合并能用上。
        merged_desc = self._merged_description(
            keeper.get("description", ""), loser.get("description", ""))
        return (True, loser["entity_id"], keeper["entity_id"],
                merged_desc, merged_aliases)

    async def _direct_merge_in_group(
        self,
        entities: List[Dict],
        entity_type: str,
        end_user_id: str,
        baseline: str,
        max_merges: int,
        snap_sink: Optional[list] = None,
    ) -> Tuple[int, set]:
        """方案B 桶内同名同类型确定性合并：分组内 name 完全相同（归一化后）的实体直接合并。

        策略：
          - 按 (归一化 name) 分桶；
          - 桶内 ≥2 个 → 度数最大的当 keeper，其余依次合到 keeper；
          - 受 max_merges 限制（与 LLM 合并共享单类型上限）；
          - 失败的子合并不影响其他子合并继续。
        snap_sink 非空时，每次成功直合追加一条 merge ChangeRecord（source=deterministic）。

        Returns:
            (合并次数, 被删除的 loser entity_id 集合)
        """
        from .deterministic.cypher_merger import (
            execute_merge, build_merged_aliases,
            # fetch_degrees_batch,  # 暂时关闭度数优先,下版打开
        )

        if not entities or max_merges <= 0:
            return 0, set()

        # 1) 按 (归一化 name, entity_type) 分桶
        # entity_type 实际由调用方保证一致（按类型分组扫描），加进 key 让代码自说明。
        buckets: Dict[Tuple[str, str], List[Dict]] = {}
        for e in entities:
            name_key = (e.get("name") or "").strip().lower()
            if not name_key:
                continue
            type_key = e.get("entity_type") or ""
            buckets.setdefault((name_key, type_key), []).append(e)

        # 度数关闭后所有实体按 0 处理,keeper 退化为桶内首个
        degrees: Dict[str, int] = {}
        # === 桶内度数批量查询（暂时关闭，下版打开）===
        # ids_for_degree: List[str] = []
        # for bucket in buckets.values():
        #     if len(bucket) >= 2:
        #         ids_for_degree.extend(e["entity_id"] for e in bucket)
        # degrees = await fetch_degrees_batch(
        #     self.connector, end_user_id, ids_for_degree)
        # ===========================================

        merged = 0
        removed_ids: set = set()
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue

            # 2) 桶内挑度数最大者为 keeper（与方案A 度数绝对优先一致）
            # 度数优先关闭时所有度数都是 0，max 会取桶内首个（Python max 稳定退化），
            # 同名同类型反正都会合到一起，keeper 选谁不影响结果。
            keeper = max(bucket, key=lambda e: degrees.get(e["entity_id"], 0))
            keeper_id = keeper["entity_id"]
            merged_name = keeper["name"]

            # 3) 桶内非 keeper 实体逐个合到 keeper
            for loser in bucket:
                if loser["entity_id"] == keeper_id:
                    continue
                if merged >= max_merges:
                    break

                merged_aliases = build_merged_aliases(keeper, loser, merged_name)
                loser_degree = degrees.get(loser["entity_id"], 0)

                merge_status = await execute_merge(
                    self.connector, end_user_id,
                    keeper_id, loser["entity_id"],
                    merged_name, merged_aliases,
                    loser_degree=loser_degree,
                    merge_max_degree=self.dedup_config.merge_max_degree,
                )
                if merge_status != "success":
                    continue

                merged += 1
                removed_ids.add(loser["entity_id"])

                # 快照：直合 merge。与高频 entity_dedup 一致：field_changes 留空，
                # 关键信息走 extra.loser_id（避免 deterministic 直合产生 old==new 噪声）
                if snap_sink is not None:
                    snap_sink.append(change(
                        "entity", "merge", target_id=keeper_id, target_name=merged_name,
                        extra={"loser_id": loser["entity_id"], "source": "deterministic",
                               "entity_type": entity_type},
                    ))

                # 写 ReflectionLog（与方案A 直合一致：source="deterministic"）
                #   注意：必须先写日志再回填本地 keeper（aliases / description），
                #   这样日志里 old=合并前快照 / new=合并后累积，能正确反映本次增量。
                self._write_dedup_log(
                    end_user_id=end_user_id,
                    keeper=keeper, loser=loser,
                    entity_type=entity_type,
                    merged_name=merged_name,
                    merged_aliases=merged_aliases,
                    confidence=1.0,
                    execution_detail={
                        "steps": [
                            {"name": "同名同类型匹配", "type": "decide", "duration_ms": 0,
                             "output": f'name="{merged_name}" type={entity_type}', "success": True},
                            {"name": "写入", "type": "write", "duration_ms": 0,
                             "output": "合并完成", "success": True},
                        ],
                        "total_ms": 0,
                        "model": "",
                    },
                    reason="同名同类型确定性合并",
                    baseline=baseline,
                    strategy="MERGE",
                    status="resolved",
                    source="deterministic",
                )

                # 同步本地 keeper 为最新累积态（与 Cypher DEDUP_MERGE_ENTITIES 一致）：
                #   - aliases：避免桶内 ≥3 个实体多次合并时丢失中间并入
                #   - description：让下一次循环的日志快照正确反映 DB 真实累积
                keeper["aliases"] = merged_aliases
                keeper["description"] = self._merged_description(
                    keeper.get("description", ""), loser.get("description", ""))

            if merged >= max_merges:
                break

        return merged, removed_ids

    #子问题6：实体描述合并
    async def _run_unresolved_resolver(self, end_user_id: str, baseline: str,
                                       language: str) -> Dict[str, Any]:
        """子问题 5：未识别实体处理（并发控制）"""
        candidates = await scan_unresolved_candidates(
            self.connector, end_user_id, batch_size=15  # 从30降至15，防止总耗时超soft_time_limit
        )
        if not candidates:
            return {"status": "success", "total": 0, "resolved": 0, "forced": 0}

        # 快照：1_input（召回的 statement 列表）+ 并发聚合 sink
        self._snap("unresolved_entity", "1_input",
                   {"batch_size": 15, "statements": candidates})
        snap_llm_raw: list = []
        snap_changes: list = []

        async def _resolve_with_limit(stmt):
            async with self._semaphore:
                return await self._resolve_one_statement(
                    stmt, end_user_id, baseline, language,
                    snap_llm_raw=snap_llm_raw, snap_changes=snap_changes,
                )

        tasks = [_resolve_with_limit(stmt) for stmt in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 快照：聚合落盘（gather 后统一写，避免并发同名文件互相覆盖）
        self._snap("unresolved_entity", "2_llm_raw", {"items": snap_llm_raw})
        self._snap_changes("unresolved_entity", snap_changes)

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
                                     baseline: str, language: str,
                                     snap_llm_raw: Optional[list] = None,
                                     snap_changes: Optional[list] = None) -> Optional[bool]:
        """处理单条 unresolved statement

        Returns:
            True = 消解成功, False = 强制提取, None = 失败（不改标记）

        snap_llm_raw / snap_changes：可选快照 sink（由 _run_unresolved_resolver 创建并在
        gather 后统一落盘）。asyncio 单线程事件循环下 list.append 在 await 间隙原子，无需加锁。
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
        # 快照：每条语句的 context + LLM 输出 + 校验结果
        if snap_llm_raw is not None:
            snap_llm_raw.append({
                "statement_id": stmt["statement_id"],
                "statement_text": stmt.get("statement_text"),
                "context_chunks": context_chunks,
                "llm_output": {
                    "resolved": result.resolved,
                    "resolution_note": result.resolution_note,
                    "entities": [e.model_dump() for e in result.entities],
                    "triplets": [t.model_dump() for t in result.triplets],
                },
                "validated": {"valid": validated.valid, "reason": validated.reason},
            })
        if not validated.valid:
            tracker.end_step(f"校验失败: {validated.reason}", success=False)
            logger.warning(
                f"未识别实体校验失败 statement={stmt['statement_id']}, "
                f"reason={validated.reason}"
            )
            if snap_changes is not None:
                snap_changes.append(change(
                    "statement", "create", target_id=stmt["statement_id"],
                    status="filtered", reason=f"validate_failed:{validated.reason}",
                ))
            return None
        tracker.end_step(
            f"有效实体 {len(validated.entities)}, 有效 triplet {len(validated.triplets)}"
        )

        # Step 4: 写入 Neo4j
        tracker.start_step("写入Neo4j", "write")
        created_entity_ids = []
        # 回收本条 statement 每个新建实体的 name_embedding（仅供快照截断展示用）：
        # 实际写库写全量向量，快照里只截前 5 维（见下方 snap_changes）。
        snap_name_embeddings: Dict[str, List[float]] = {}
        # 同 statement 内所有派生实体/边共用一个 run_id：优先复用来源 statement 的 run_id，
        # 老数据为空时一次性兜底，避免实体和关系边拿到不同的随机 run_id。
        fallback_run_id = stmt.get("run_id") or uuid.uuid4().hex

        # 4.1 创建实体
        for entity in validated.entities:
            # "用户"实体不重新创建（全局用户节点已存在），改为把本次 LLM 输出的
            # description 累加到全局用户节点，避免反思补救出来的语义被丢弃。
            # description 用 '；' 拼接，与 DEDUP_MERGE_ENTITIES 的语义保持一致。
            if entity.name.strip() == "用户":
                if entity.description:
                    try:
                        await self.connector.execute_query(
                            UNRESOLVED_APPEND_USER_INFO,
                            end_user_id=end_user_id,
                            description=entity.description,
                        )
                    except Exception as user_err:
                        logger.warning(
                            f"追加用户实体描述失败 end_user={end_user_id}: {user_err}"
                        )
                continue
            entity_result = await self.connector.execute_query(
                UNRESOLVED_CREATE_ENTITY,
                end_user_id=end_user_id,
                name=entity.name,
                entity_type=entity.type,
                description=entity.description,
                run_id=fallback_run_id,
                type_id=entity.type_id,
                type_description=entity.type_description,
                entity_idx=entity.entity_idx,
                is_explicit_memory=entity.is_explicit_memory,
                statement_id=stmt["statement_id"],
                created_at=utcnow_naive(),
            )
            if entity_result:
                entity_id = entity_result[0].get("entity_id", "")
                created_entity_ids.append(entity_id)
                # 补 name_embedding
                if self.embedding_client:
                    try:
                        name_embedding = self.embedding_client.embed_query(entity.name)
                        if name_embedding:
                            snap_name_embeddings[entity.name] = name_embedding
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
                    predicate_description=triplet.predicate_description,
                    statement_id=stmt["statement_id"],
                    valid_at=triplet.valid_at,
                    invalid_at=triplet.invalid_at,
                    run_id=fallback_run_id,
                    created_at=utcnow_naive(),
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
                run_id=fallback_run_id,
                created_at=utcnow_naive(),
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

        # 快照：本条 statement 的字段级变更（实体创建/用户追加/关系边/标记）
        if snap_changes is not None:
            for e in validated.entities:
                if e.name.strip() == "用户":
                    snap_changes.append(change(
                        "entity", "append_desc", target_name="用户",
                        field_changes=[{"field": "description", "old": None, "new": e.description}],
                        extra={"statement_id": stmt["statement_id"]},
                    ))
                else:
                    _fc = [
                        {"field": "name", "old": None, "new": e.name},
                        {"field": "entity_type", "old": None, "new": e.type},
                        {"field": "description", "old": None, "new": e.description},
                    ]
                    # name_embedding 实际写全量向量，快照只留前 5 维便于人工核对"是否补了向量"
                    _emb = snap_name_embeddings.get(e.name)
                    if _emb:
                        _fc.append({"field": "name_embedding", "old": None,
                                    "new": ReflectionSnapshotRecorder.truncate_vectors(_emb, 5)})
                    snap_changes.append(change(
                        "entity", "create", target_name=e.name,
                        field_changes=_fc,
                        extra={"statement_id": stmt["statement_id"],
                               "run_id": fallback_run_id, "type_id": e.type_id},
                    ))
            for t in validated.triplets:
                snap_changes.append(change(
                    "edge", "create",
                    target_name=f"{t.subject_name} -[{t.predicate}]-> {t.object_name}",
                    field_changes=[
                        {"field": "subject", "old": None, "new": t.subject_name},
                        {"field": "predicate", "old": None, "new": t.predicate},
                        {"field": "object", "old": None, "new": t.object_name},
                    ],
                    extra={"run_id": fallback_run_id, "predicate_id": t.predicate_id},
                ))
            snap_changes.append(change(
                "statement", "mark", target_id=stmt["statement_id"],
                field_changes=[{"field": "unresolved_flag", "old": True, "new": False}],
            ))

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
            f"operations={len(result.operations)}, "
            f"rename={result.should_rename_entity}"
        )

        # 快照：1_input（碎片 + 已有 summary/timeline/event_timeline）+ 2_llm_raw（LLM 原始输出）
        self._snap("description_merge", "1_input", {
            "entity_id": entity["entity_id"], "name": entity["name"],
            "fragment_count": len(fragments),
            "description": description,
            "description_summary": existing_summary,
            "description_timeline": existing_timeline,
            "event_timeline": _parse_timeline_full(existing_event_timeline),
        })
        self._snap("description_merge", "2_llm_raw", {
            "entity_id": entity["entity_id"],
            "description_summary": result.description_summary,
            "operations": [o.model_dump() for o in result.operations],
            "should_rename_entity": result.should_rename_entity,
            "suggested_entity_name": result.suggested_entity_name,
        })

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

        # Step 4: 应用事件操作（add/delete/update）
        tracker.start_step("事件过滤", "decide")
        valid_ops = validate_event_operations(result.operations)
        # 被 validator 丢弃的 op（filtered）：result.operations 里不在 valid_ops 的
        valid_ids = {id(o) for o in valid_ops}
        filtered_ops = [
            {"op": (o.op or ""), "status": "filtered", "reason": "validator_invalid"}
            for o in result.operations if id(o) not in valid_ids
        ]
        before_events = _parse_timeline_full(existing_event_timeline)
        event_timeline, ev_stats, op_trace = apply_event_operations(
            existing_event_timeline, valid_ops, collect_trace=True
        )
        tracker.end_step(
            f"事件操作 新增{ev_stats['added']} 更新{ev_stats['updated']} 删除{ev_stats['deleted']}"
        )

        # 快照：事件时间线 before/after 全文 + 逐 op 命运（最关键的核对项）
        self._snap("description_merge", "4_event_timeline_diff", {
            "entity_id": entity["entity_id"],
            "before": before_events,
            "after": _parse_timeline_full(event_timeline),
            "stats": ev_stats,
            "operations": filtered_ops + op_trace,
        })

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
        rename_status = None
        old_name = entity["name"]
        if result.should_rename_entity and result.suggested_entity_name:
            rename_status = await self._try_rename_entity(
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

        # 快照：3_changes（summary/description 清空/timeline 备份 + 事件命运 + 更名）
        snap_changes = [
            change("entity", "update_field", target_id=entity["entity_id"],
                   target_name=entity["name"],
                   field_changes=[{"field": "description_summary",
                                   "old": existing_summary or "", "new": merged_text}]),
            change("entity", "update_field", target_id=entity["entity_id"],
                   target_name=entity["name"],
                   field_changes=[{"field": "description", "old": description, "new": ""}],
                   reason="cleared_after_summary"),
            change("entity", "update_field", target_id=entity["entity_id"],
                   target_name=entity["name"],
                   field_changes=[{"field": "description_timeline",
                                   "old": existing_timeline or "", "new": timeline}]),
        ]
        # event_timeline 是实体的单个字符串属性（序列化后整体存回实体）：本轮事件
        # 增删改最终落成它的一次字段级更新，只在确有变化时记一条 update_field，
        # 不把单个属性拆成多条 event 记录，也避免 old==new 的无意义对比。
        if (existing_event_timeline or "") != (event_timeline or ""):
            snap_changes.append(change(
                "entity", "update_field", target_id=entity["entity_id"],
                target_name=entity["name"],
                field_changes=[{"field": "event_timeline",
                                "old": existing_event_timeline or "",
                                "new": event_timeline}],
            ))
        # 更名命运（rename_status: None=未触发 / applied / skipped:* / rejected:*）
        if rename_status:
            st = "applied" if rename_status == "applied" else (
                "rejected" if rename_status.startswith("rejected") else "skipped")
            snap_changes.append(change(
                "entity", "rename", target_id=entity["entity_id"], target_name=old_name,
                field_changes=[{"field": "name", "old": old_name,
                                "new": result.suggested_entity_name}],
                status=st, reason=(None if st == "applied" else rename_status),
            ))
        self._snap_changes("description_merge", snap_changes)
        return True

    async def _try_rename_entity(self, entity: Dict, suggested_name: str,
                                 end_user_id: str):
        """尝试更名实体，含兜底校验。

        返回更名命运字符串：applied / skipped:empty / skipped:same_name /
        skipped:is_user / rejected:conflict（供快照记录，原调用方可忽略返回值）。
        """
        old_name = entity["name"]

        # 兜底校验
        if not suggested_name or not suggested_name.strip():
            return "skipped:empty"
        if suggested_name.strip() == old_name:
            return "skipped:same_name"
        if old_name == "用户" or suggested_name.strip() == "用户":
            return "skipped:is_user"

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
            return "rejected:conflict"

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
        return "applied"
