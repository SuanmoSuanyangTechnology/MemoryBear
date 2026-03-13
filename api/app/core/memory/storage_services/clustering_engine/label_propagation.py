"""标签传播聚类引擎

基于 ZEP 论文的动态标签传播算法，对 Neo4j 中的 ExtractedEntity 节点进行社区聚类。

支持两种模式：
- 全量初始化（full_clustering）：首次运行，对所有实体做完整 LPA 迭代
- 增量更新（incremental_update）：新实体到达时，只处理新实体及其邻居
"""

import logging
import uuid
from math import sqrt
from typing import Dict, List, Optional

from app.repositories.neo4j.community_repository import CommunityRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)

# 全量迭代最大轮数，防止不收敛
MAX_ITERATIONS = 10
# 社区摘要核心实体数量
CORE_ENTITY_LIMIT = 5


def _cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
    """计算两个向量的余弦相似度，任一为空则返回 0。"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sqrt(sum(a * a for a in v1))
    norm2 = sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _weighted_vote(
    neighbors: List[Dict],
    self_embedding: Optional[List[float]],
) -> Optional[str]:
    """
    加权多数投票，选出得票最高的社区。

    权重 = 语义相似度（name_embedding 余弦）* activation_value 加成
    没有 community_id 的邻居不参与投票。
    """
    votes: Dict[str, float] = {}
    for nb in neighbors:
        cid = nb.get("community_id")
        if not cid:
            continue
        sem = _cosine_similarity(self_embedding, nb.get("name_embedding"))
        act = nb.get("activation_value") or 0.5
        # 语义相似度权重 0.6，激活值权重 0.4
        weight = 0.6 * sem + 0.4 * act
        votes[cid] = votes.get(cid, 0.0) + weight

    if not votes:
        return None
    return max(votes, key=votes.__getitem__)


class LabelPropagationEngine:
    """标签传播聚类引擎"""

    def __init__(
        self,
        connector: Neo4jConnector,
        config_id: Optional[str] = None,
        llm_model_id: Optional[str] = None,
    ):
        self.connector = connector
        self.repo = CommunityRepository(connector)
        self.config_id = config_id
        self.llm_model_id = llm_model_id

    # ──────────────────────────────────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────────────────────────────────

    async def run(
        self,
        end_user_id: str,
        new_entity_ids: Optional[List[str]] = None,
    ) -> None:
        """
        统一入口：自动判断全量还是增量。

        - 若该用户尚无 Community 节点 → 全量初始化
        - 否则 → 增量更新（仅处理 new_entity_ids）
        """
        has_communities = await self.repo.has_communities(end_user_id)
        if not has_communities:
            logger.info(f"[Clustering] 用户 {end_user_id} 首次聚类，执行全量初始化")
            await self.full_clustering(end_user_id)
        else:
            if new_entity_ids:
                logger.info(
                    f"[Clustering] 增量更新，新实体数: {len(new_entity_ids)}"
                )
                await self.incremental_update(new_entity_ids, end_user_id)

    async def full_clustering(self, end_user_id: str) -> None:
        """
        全量标签传播初始化。

        1. 拉取所有实体，初始化每个实体为独立社区
        2. 迭代：每轮对所有实体做邻居投票，更新社区标签
        3. 直到标签不再变化或达到 MAX_ITERATIONS
        4. 将最终标签写入 Neo4j
        """
        entities = await self.repo.get_all_entities(end_user_id)
        if not entities:
            logger.info(f"[Clustering] 用户 {end_user_id} 无实体，跳过全量聚类")
            return

        # 初始化：每个实体持有自己 id 作为社区标签
        labels: Dict[str, str] = {e["id"]: e["id"] for e in entities}
        embeddings: Dict[str, Optional[List[float]]] = {
            e["id"]: e.get("name_embedding") for e in entities
        }

        for iteration in range(MAX_ITERATIONS):
            changed = 0
            # 随机顺序（Python dict 在 3.7+ 保持插入顺序，这里直接遍历）
            for entity in entities:
                eid = entity["id"]
                neighbors = await self.repo.get_entity_neighbors(eid, end_user_id)

                # 将邻居的当前内存标签注入（覆盖 Neo4j 中的旧值）
                enriched = []
                for nb in neighbors:
                    nb_copy = dict(nb)
                    nb_copy["community_id"] = labels.get(nb["id"], nb.get("community_id"))
                    enriched.append(nb_copy)

                new_label = _weighted_vote(enriched, embeddings.get(eid))
                if new_label and new_label != labels[eid]:
                    labels[eid] = new_label
                    changed += 1

            logger.info(
                f"[Clustering] 全量迭代 {iteration + 1}/{MAX_ITERATIONS}，"
                f"标签变化数: {changed}"
            )
            if changed == 0:
                logger.info("[Clustering] 标签已收敛，提前结束迭代")
                break

        # 将最终标签写入 Neo4j
        await self._flush_labels(labels, end_user_id)
        pre_merge_count = len(set(labels.values()))
        logger.info(
            f"[Clustering] 全量迭代完成，共 {pre_merge_count} 个社区，"
            f"{len(labels)} 个实体，开始后处理合并"
        )

        # 全量初始化后做一轮社区合并（基于 name_embedding 余弦相似度）
        all_community_ids = list(set(labels.values()))
        await self._evaluate_merge(all_community_ids, end_user_id)

        logger.info(
            f"[Clustering] 全量聚类完成，合并前 {pre_merge_count} 个社区，"
            f"{len(labels)} 个实体"
        )
        # 为所有社区生成元数据
        # 注意：_evaluate_merge 后部分社区已被合并消解，需重新从 Neo4j 查询实际存活的社区
        # 不能复用 labels.values()，那里包含已被 dissolve 的旧社区 ID
        surviving_communities = await self.repo.get_all_entities(end_user_id)
        surviving_community_ids = list({
            e.get("community_id") for e in surviving_communities
            if e.get("community_id")
        })
        logger.info(f"[Clustering] 合并后实际存活社区数: {len(surviving_community_ids)}")
        for cid in surviving_community_ids:
            await self._generate_community_metadata(cid, end_user_id)

    async def incremental_update(
        self, new_entity_ids: List[str], end_user_id: str
    ) -> None:
        """
        增量更新：只处理新实体及其邻居，不重跑全图。

        1. 对每个新实体查询邻居
        2. 加权多数投票决定社区归属
        3. 若邻居无社区 → 创建新社区
        4. 若邻居分属多个社区 → 评估是否合并
        """
        for entity_id in new_entity_ids:
            await self._process_single_entity(entity_id, end_user_id)

    # ──────────────────────────────────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────────────────────────────────

    async def _process_single_entity(
        self, entity_id: str, end_user_id: str
    ) -> None:
        """处理单个新实体的社区分配。"""
        neighbors = await self.repo.get_entity_neighbors(entity_id, end_user_id)

        # 查询自身 embedding（从邻居查询结果中无法获取，需单独查）
        self_embedding = await self._get_entity_embedding(entity_id, end_user_id)

        if not neighbors:
            # 孤立实体：创建单成员社区
            new_cid = self._new_community_id()
            await self.repo.upsert_community(new_cid, end_user_id, member_count=1)
            await self.repo.assign_entity_to_community(entity_id, new_cid, end_user_id)
            logger.debug(f"[Clustering] 孤立实体 {entity_id} → 新社区 {new_cid}")
            return

        # 统计邻居社区分布
        community_ids_in_neighbors = set(
            nb["community_id"] for nb in neighbors if nb.get("community_id")
        )

        target_cid = _weighted_vote(neighbors, self_embedding)

        if target_cid is None:
            # 邻居都没有社区，连同新实体一起创建新社区
            new_cid = self._new_community_id()
            await self.repo.upsert_community(new_cid, end_user_id)
            await self.repo.assign_entity_to_community(entity_id, new_cid, end_user_id)
            for nb in neighbors:
                await self.repo.assign_entity_to_community(
                    nb["id"], new_cid, end_user_id
                )
            await self.repo.refresh_member_count(new_cid, end_user_id)
            logger.debug(
                f"[Clustering] 新实体 {entity_id} 与 {len(neighbors)} 个无社区邻居 → 新社区 {new_cid}"
            )
            await self._generate_community_metadata(new_cid, end_user_id)
        else:
            # 加入得票最多的社区
            await self.repo.assign_entity_to_community(entity_id, target_cid, end_user_id)
            await self.repo.refresh_member_count(target_cid, end_user_id)
            logger.debug(f"[Clustering] 新实体 {entity_id} → 社区 {target_cid}")

            # 若邻居分属多个社区，评估合并
            if len(community_ids_in_neighbors) > 1:
                await self._evaluate_merge(
                    list(community_ids_in_neighbors), end_user_id
                )
            await self._generate_community_metadata(target_cid, end_user_id)

    async def _evaluate_merge(
        self, community_ids: List[str], end_user_id: str
    ) -> None:
        """
        评估多个社区是否应合并。

        策略：计算各社区成员 embedding 的平均向量，若两两余弦相似度 > 0.75 则合并。
        合并时保留成员数最多的社区，其余成员迁移过来。

        全量场景（社区数 > 20）使用批量查询，避免 N 次数据库往返。
        """
        MERGE_THRESHOLD = 0.85
        BATCH_THRESHOLD = 20  # 超过此数量走批量查询

        community_embeddings: Dict[str, Optional[List[float]]] = {}
        community_sizes: Dict[str, int] = {}

        if len(community_ids) > BATCH_THRESHOLD:
            # 批量查询：一次拉取所有社区成员
            all_members = await self.repo.get_all_community_members_batch(
                community_ids, end_user_id
            )
            for cid in community_ids:
                members = all_members.get(cid, [])
                community_sizes[cid] = len(members)
                valid_embeddings = [
                    m["name_embedding"] for m in members if m.get("name_embedding")
                ]
                if valid_embeddings:
                    dim = len(valid_embeddings[0])
                    community_embeddings[cid] = [
                        sum(e[i] for e in valid_embeddings) / len(valid_embeddings)
                        for i in range(dim)
                    ]
                else:
                    community_embeddings[cid] = None
        else:
            # 增量场景：逐个查询
            for cid in community_ids:
                members = await self.repo.get_community_members(cid, end_user_id)
                community_sizes[cid] = len(members)
                valid_embeddings = [
                    m["name_embedding"] for m in members if m.get("name_embedding")
                ]
                if valid_embeddings:
                    dim = len(valid_embeddings[0])
                    community_embeddings[cid] = [
                        sum(e[i] for e in valid_embeddings) / len(valid_embeddings)
                        for i in range(dim)
                    ]
                else:
                    community_embeddings[cid] = None

        # 找出应合并的社区对
        to_merge: List[tuple] = []
        cids = list(community_ids)
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                sim = _cosine_similarity(
                    community_embeddings[cids[i]],
                    community_embeddings[cids[j]],
                )
                if sim > MERGE_THRESHOLD:
                    to_merge.append((cids[i], cids[j]))

        logger.info(f"[Clustering] 发现 {len(to_merge)} 对可合并社区")

        # 执行合并：逐对处理，每次合并后重新计算合并社区的平均向量
        # 避免 union-find 链式传递导致语义不相关的社区被间接合并
        # （A≈B、B≈C 不代表 A≈C，不能因传递性把 A/B/C 全部合并）
        merged_into: Dict[str, str] = {}  # dissolve → keep 的最终映射

        def get_root(x: str) -> str:
            """路径压缩，找到 x 当前所属的根社区。"""
            while x in merged_into:
                merged_into[x] = merged_into.get(merged_into[x], merged_into[x])
                x = merged_into[x]
            return x

        for c1, c2 in to_merge:
            root1, root2 = get_root(c1), get_root(c2)
            if root1 == root2:
                continue

            # 用合并后的最新平均向量重新验证相似度
            # 防止链式传递：A≈B 合并后 B 的向量已更新，C 必须和新 B 相似才能合并
            current_sim = _cosine_similarity(
                community_embeddings.get(root1),
                community_embeddings.get(root2),
            )
            if current_sim <= MERGE_THRESHOLD:
                # 合并后向量已漂移，不再满足阈值，跳过
                logger.debug(
                    f"[Clustering] 跳过合并 {root1} ↔ {root2}，"
                    f"当前相似度 {current_sim:.3f} ≤ {MERGE_THRESHOLD}"
                )
                continue

            keep = root1 if community_sizes.get(root1, 0) >= community_sizes.get(root2, 0) else root2
            dissolve = root2 if keep == root1 else root1
            merged_into[dissolve] = keep

            members = await self.repo.get_community_members(dissolve, end_user_id)
            for m in members:
                await self.repo.assign_entity_to_community(m["id"], keep, end_user_id)

            # 合并后重新计算 keep 的平均向量（加权平均）
            keep_emb = community_embeddings.get(keep)
            dissolve_emb = community_embeddings.get(dissolve)
            keep_size = community_sizes.get(keep, 0)
            dissolve_size = community_sizes.get(dissolve, 0)
            total_size = keep_size + dissolve_size
            if keep_emb and dissolve_emb and total_size > 0:
                dim = len(keep_emb)
                community_embeddings[keep] = [
                    (keep_emb[i] * keep_size + dissolve_emb[i] * dissolve_size) / total_size
                    for i in range(dim)
                ]
            community_embeddings[dissolve] = None

            community_sizes[keep] = total_size
            community_sizes[dissolve] = 0
            await self.repo.refresh_member_count(keep, end_user_id)
            logger.info(
                f"[Clustering] 社区合并: {dissolve} → {keep}，"
                f"相似度={current_sim:.3f}，迁移 {len(members)} 个成员"
            )

    async def _flush_labels(
        self, labels: Dict[str, str], end_user_id: str
    ) -> None:
        """将内存中的标签批量写入 Neo4j。"""
        # 先创建所有唯一社区节点
        unique_communities = set(labels.values())
        for cid in unique_communities:
            await self.repo.upsert_community(cid, end_user_id)

        # 再批量分配实体
        for entity_id, community_id in labels.items():
            await self.repo.assign_entity_to_community(
                entity_id, community_id, end_user_id
            )

        # 刷新成员数
        for cid in unique_communities:
            await self.repo.refresh_member_count(cid, end_user_id)

    async def _get_entity_embedding(
        self, entity_id: str, end_user_id: str
    ) -> Optional[List[float]]:
        """查询单个实体的 name_embedding。"""
        try:
            result = await self.connector.execute_query(
                "MATCH (e:ExtractedEntity {id: $eid, end_user_id: $uid}) "
                "RETURN e.name_embedding AS name_embedding",
                eid=entity_id,
                uid=end_user_id,
            )
            return result[0]["name_embedding"] if result else None
        except Exception:
            return None

    async def _generate_community_metadata(
        self, community_id: str, end_user_id: str
    ) -> None:
        """
        为社区生成并写入元数据：名称、摘要、核心实体。

        - core_entities：按 activation_value 排序取 top-N 实体名称列表（无需 LLM）
        - name / summary：若有 llm_model_id 则调用 LLM 生成，否则用实体名称拼接兜底
        """
        try:
            members = await self.repo.get_community_members(community_id, end_user_id)
            if not members:
                return

            # 核心实体：按 activation_value 降序取 top-N
            sorted_members = sorted(
                members,
                key=lambda m: m.get("activation_value") or 0,
                reverse=True,
            )
            core_entities = [m["name"] for m in sorted_members[:CORE_ENTITY_LIMIT] if m.get("name")]
            all_names = [m["name"] for m in members if m.get("name")]

            name = "、".join(core_entities[:3]) if core_entities else community_id[:8]
            summary = f"包含实体：{', '.join(all_names)}"

            # 若有 LLM 配置，调用 LLM 生成更好的名称和摘要
            if self.llm_model_id:
                try:
                    from app.db import get_db_context
                    from app.core.memory.utils.llm.llm_utils import MemoryClientFactory

                    entity_list_str = "、".join(all_names)
                    prompt = (
                        f"以下是一组语义相关的实体：{entity_list_str}\n\n"
                        f"请为这组实体所代表的主题：\n"
                        f"1. 起一个简洁的中文名称（不超过10个字）\n"
                        f"2. 写一句话摘要（不超过50个字）\n\n"
                        f"严格按以下格式输出，不要有其他内容：\n"
                        f"名称：<名称>\n摘要：<摘要>"
                    )
                    with get_db_context() as db:
                        factory = MemoryClientFactory(db)
                        llm_client = factory.get_llm_client(self.llm_model_id)
                        response = await llm_client.chat([{"role": "user", "content": prompt}])
                        text = response.content if hasattr(response, "content") else str(response)

                    for line in text.strip().splitlines():
                        if line.startswith("名称："):
                            name = line[3:].strip()
                        elif line.startswith("摘要："):
                            summary = line[3:].strip()
                except Exception as e:
                    logger.warning(f"[Clustering] LLM 生成社区元数据失败，使用兜底值: {e}")

            await self.repo.update_community_metadata(
                community_id=community_id,
                end_user_id=end_user_id,
                name=name,
                summary=summary,
                core_entities=core_entities,
            )
            logger.debug(f"[Clustering] 社区 {community_id} 元数据已更新: name={name}")
        except Exception as e:
            logger.error(f"[Clustering] _generate_community_metadata failed for {community_id}: {e}")

    @staticmethod
    def _new_community_id() -> str:
        return str(uuid.uuid4())
