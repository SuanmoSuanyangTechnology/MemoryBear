"""
嵌入向量生成器

为陈述句、分块、对话和实体生成嵌入向量，用于语义搜索。
"""

import asyncio
from typing import List, Dict, Any, Tuple
from app.core.memory.models.message_models import DialogData
from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.memory.utils.config.config_utils import get_embedder_config
from app.core.models.base import RedBearModelConfig


class EmbeddingGenerator:
    """嵌入向量生成器"""

    def __init__(self, embedding_id: str):
        """初始化嵌入向量生成器

        Args:
            embedding_id: 嵌入模型 ID
        """
        embedder_config = get_embedder_config(embedding_id)
        self.embedder_client = OpenAIEmbedderClient(
            model_config=RedBearModelConfig.model_validate(embedder_config),
        )

    async def _generate_embeddings(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """生成一批文本的嵌入向量（支持分批并行）

        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数量（默认 100）

        Returns:
            嵌入向量列表
        """
        if not texts:
            return []
        
        # 如果文本数量小于批次大小，直接处理
        if len(texts) <= batch_size:
            return await self.embedder_client.response(texts)
        
        # 分批并行处理
        print(f"文本数量 {len(texts)} 超过批次大小 {batch_size}，分批并行处理")
        batches = [texts[i:i+batch_size] for i in range(0, len(texts), batch_size)]
        print(f"分成 {len(batches)} 批，每批最多 {batch_size} 个文本")
        
        # 并行发送所有批次
        batch_results = await asyncio.gather(*[
            self.embedder_client.response(batch) for batch in batches
        ])
        
        # 合并结果
        embeddings = []
        for batch_result in batch_results:
            embeddings.extend(batch_result)
        
        print(f"分批并行处理完成，共生成 {len(embeddings)} 个嵌入向量")
        return embeddings

    async def generate_statement_embeddings(
        self,
        chunked_dialogs: List[DialogData]
    ) -> List[Dict[str, List[float]]]:
        """为所有对话中的陈述句生成嵌入向量

        Args:
            chunked_dialogs: 包含分块和陈述句的对话列表

        Returns:
            每个对话的陈述句嵌入向量映射列表
        """
        print("\n=== 生成陈述句嵌入向量 ===")

        # 收集所有陈述句
        all_statements = []
        statement_to_dialog_chunk_map = []

        for d_idx, dialog in enumerate(chunked_dialogs):
            chunks = dialog.chunks
            if asyncio.iscoroutine(chunks):
                chunks = await chunks
            for c_idx, chunk in enumerate(chunks):
                for s_idx, stmt in enumerate(chunk.statements):
                    all_statements.append(stmt.statement)
                    statement_to_dialog_chunk_map.append((d_idx, c_idx, s_idx))

        # 批量生成嵌入向量
        stmt_embeddings = await self._generate_embeddings(all_statements)

        # 创建映射
        stmt_embedding_maps = [{} for _ in chunked_dialogs]
        for idx, embedding in enumerate(stmt_embeddings):
            d_idx, c_idx, s_idx = statement_to_dialog_chunk_map[idx]
            stmt_id = chunked_dialogs[d_idx].chunks[c_idx].statements[s_idx].id
            stmt_embedding_maps[d_idx][stmt_id] = embedding

        print(f"为 {len(all_statements)} 个陈述句生成了嵌入向量")
        return stmt_embedding_maps

    async def generate_chunk_embeddings(
        self,
        chunked_dialogs: List[DialogData]
    ) -> List[Dict[str, List[float]]]:
        """为所有对话中的分块生成嵌入向量

        Args:
            chunked_dialogs: 包含分块的对话列表

        Returns:
            每个对话的分块嵌入向量映射列表
        """
        print("\n=== 生成分块嵌入向量 ===")

        # 收集所有分块
        all_chunks = []
        chunk_to_dialog_map = []

        for d_idx, dialog in enumerate(chunked_dialogs):
            for c_idx, chunk in enumerate(dialog.chunks):
                all_chunks.append(chunk.content)
                chunk_to_dialog_map.append((d_idx, c_idx))

        # 批量生成嵌入向量
        chunk_embeddings = await self._generate_embeddings(all_chunks)

        # 创建映射
        chunk_embedding_maps = [{} for _ in chunked_dialogs]
        for idx, embedding in enumerate(chunk_embeddings):
            d_idx, c_idx = chunk_to_dialog_map[idx]
            chunk_id = chunked_dialogs[d_idx].chunks[c_idx].id
            chunk_embedding_maps[d_idx][chunk_id] = embedding

        print(f"为 {len(all_chunks)} 个分块生成了嵌入向量")
        return chunk_embedding_maps

    async def generate_dialog_embeddings(
        self,
        chunked_dialogs: List[DialogData]
    ) -> List[List[float]]:
        """为对话生成嵌入向量（当前跳过，返回空列表）

        Args:
            chunked_dialogs: 对话列表

        Returns:
            对话嵌入向量列表（当前为空）
        """
        # 跳过对话嵌入向量生成，但保持正确的长度
        return [[] for _ in chunked_dialogs]

    async def generate_all_embeddings(
        self,
        chunked_dialogs: List[DialogData]
    ) -> Tuple[
        List[Dict[str, List[float]]],
        List[Dict[str, List[float]]],
        List[List[float]]
    ]:
        """生成所有类型的嵌入向量

        Args:
            chunked_dialogs: 包含分块和陈述句的对话列表

        Returns:
            (陈述句嵌入映射列表, 分块嵌入映射列表, 对话嵌入列表)
        """
        print("\n=== 生成所有嵌入向量 ===")

        # 并发生成陈述句和分块嵌入向量
        stmt_embedding_maps, chunk_embedding_maps = await asyncio.gather(
            self.generate_statement_embeddings(chunked_dialogs),
            self.generate_chunk_embeddings(chunked_dialogs)
        )

        # 对话嵌入向量（当前跳过）
        dialog_embeddings = await self.generate_dialog_embeddings(chunked_dialogs)

        print(
            f"生成完成：{len(chunked_dialogs)} 个对话的嵌入向量"
        )

        return stmt_embedding_maps, chunk_embedding_maps, dialog_embeddings

    async def generate_entity_embeddings(
        self,
        triplet_maps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """为三元组中的实体生成嵌入向量

        Args:
            triplet_maps: 三元组映射列表

        Returns:
            更新后的三元组映射列表（实体包含嵌入向量）
        """
        print("\n=== 生成实体嵌入向量 ===")

        entity_texts: List[str] = []
        entity_refs: List[Any] = []

        # 收集所有实体
        for trip_map in triplet_maps:
            for _, triplet_info in trip_map.items():
                entities = getattr(triplet_info, "entities", None)
                if not entities:
                    continue
                for ent in entities:
                    text = getattr(ent, "name", None) or getattr(ent, "description", None)
                    if text:
                        entity_texts.append(text)
                        entity_refs.append(ent)

        if not entity_texts:
            print("没有找到需要生成嵌入向量的实体")
            return triplet_maps

        # 批量生成嵌入向量
        embeddings = await self._generate_embeddings(entity_texts)

        # 打印前几个嵌入向量的维度
        for i in range(min(5, len(embeddings))):
            print(f"实体 '{entity_texts[i]}' 嵌入向量维度: {len(embeddings[i])}")

        # 将嵌入向量赋值给实体
        for ent, emb in zip(entity_refs, embeddings):
            setattr(ent, "name_embedding", emb)

        print(f"为 {len(entity_refs)} 个实体生成了嵌入向量")
        return triplet_maps


# 保持向后兼容的函数接口
async def embedding_generation(
    chunked_dialogs: List[DialogData],
    embedding_id: str
) -> Tuple[
    List[Dict[str, List[float]]],
    List[Dict[str, List[float]]],
    List[List[float]]
]:
    """生成陈述句、分块和对话的嵌入向量（向后兼容接口）

    Args:
        chunked_dialogs: 包含分块和陈述句的对话列表
        embedding_id: 嵌入模型 ID

    Returns:
        (陈述句嵌入映射列表, 分块嵌入映射列表, 对话嵌入列表)
    """
    generator = EmbeddingGenerator(embedding_id)
    return await generator.generate_all_embeddings(chunked_dialogs)


async def generate_entity_embeddings_from_triplets(
    triplet_maps: List[Dict[str, Any]],
    embedding_id: str
) -> List[Dict[str, Any]]:
    """为三元组中的实体生成嵌入向量（向后兼容接口）

    Args:
        triplet_maps: 三元组映射列表
        embedding_id: 嵌入模型 ID

    Returns:
        更新后的三元组映射列表（实体包含嵌入向量）
    """
    generator = EmbeddingGenerator(embedding_id)
    return await generator.generate_entity_embeddings(triplet_maps)


async def embedding_generation_all(
    chunked_dialogs: List[DialogData],
    triplet_maps: List[Dict[str, Any]],
    embedding_id: str
) -> Tuple[
    List[Dict[str, List[float]]],
    List[Dict[str, List[float]]],
    List[List[float]],
    List[Dict[str, Any]]
]:
    """生成所有类型的嵌入向量（向后兼容接口）

    Args:
        chunked_dialogs: 包含分块和陈述句的对话列表
        triplet_maps: 三元组映射列表
        embedding_id: 嵌入模型 ID

    Returns:
        (陈述句嵌入映射列表, 分块嵌入映射列表, 对话嵌入列表, 更新后的三元组映射列表)
    """
    print("\n=== 综合嵌入向量生成（陈述句/分块/对话 + 实体）===")

    generator = EmbeddingGenerator(embedding_id)

    # 生成陈述句、分块和对话的嵌入向量
    stmt_embedding_maps, chunk_embedding_maps, dialog_embeddings = await generator.generate_all_embeddings(
        chunked_dialogs
    )

    # 生成实体嵌入向量
    updated_triplet_maps = await generator.generate_entity_embeddings(triplet_maps)

    return stmt_embedding_maps, chunk_embedding_maps, dialog_embeddings, updated_triplet_maps
