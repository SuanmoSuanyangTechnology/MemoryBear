from typing import Any, List
import re
import os
import asyncio
import json
import numpy as np
import logging

# Fix tokenizer parallelism warning
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from chonkie import (
    SemanticChunker,
    RecursiveChunker,
    RecursiveRules,
    LateChunker,
    NeuralChunker,
    SentenceChunker,
    TokenChunker,
)

from app.core.memory.models.config_models import ChunkerConfig
from app.core.memory.models.message_models import DialogData, Chunk
try:
    from app.core.memory.llm_tools.openai_client import OpenAIClient
except Exception:
    OpenAIClient = Any

# Initialize logger
logger = logging.getLogger(__name__)


class LLMChunker:
    """LLM-based intelligent chunking strategy"""
    def __init__(self, llm_client: OpenAIClient, chunk_size: int = 1000):
        self.llm_client = llm_client
        self.chunk_size = chunk_size

    async def __call__(self, text: str) -> List[Any]:
        prompt = f"""
            Split the following text into semantically coherent paragraphs. Each paragraph should focus on one topic, approximately {self.chunk_size} characters long.
            Return results in JSON format with a chunks array, each chunk having a text field.

            Text content:
            {text[:5000]}
            """

        messages = [
            {"role": "system", "content": "You are a professional text analysis assistant, skilled at splitting long texts into semantically coherent paragraphs."},
            {"role": "user", "content": prompt}
        ]

        try:
            # 使用异步的 achat 方法
            if hasattr(self.llm_client, 'achat'):
                response = await self.llm_client.achat(messages)
            else:
                # 如果没有异步方法，使用同步方法并转换为异步
                response = await asyncio.to_thread(self.llm_client.chat, messages)

            # 检查响应格式并提取内容
            if hasattr(response, 'choices') and len(response.choices) > 0:
                content = response.choices[0].message.content
            elif hasattr(response, 'content'):
                content = response.content
            else:
                content = str(response)

            # 解析LLM响应
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content

            result = json.loads(json_str)

            class SimpleChunk:
                def __init__(self, text, index):
                    self.text = text
                    self.start_index = index * 100  # 近似位置
                    self.end_index = (index + 1) * 100

            return [SimpleChunk(chunk["text"], i) for i, chunk in enumerate(result.get("chunks", []))]

        except Exception as e:
            print(f"LLM分块失败: {e}")
            # 失败时返回空列表，外层会处理回退方案
            return []


class HybridChunker:
    """混合分块策略：先按结构分块，再按语义合并"""
    def __init__(self, semantic_threshold: float = 0.8, base_chunk_size: int = 300):
        self.semantic_threshold = semantic_threshold
        self.base_chunk_size = base_chunk_size
        self.base_chunker = TokenChunker(tokenizer="character", chunk_size=base_chunk_size)
        self.semantic_chunker = SemanticChunker(threshold=semantic_threshold)

    def __call__(self, text: str) -> List[Any]:
        # 先用基础分块
        base_chunks = self.base_chunker(text)

        # 如果文本不长，直接返回基础分块
        if len(base_chunks) <= 3:
            return base_chunks

        # 对基础分块进行语义合并
        combined_text = " ".join([chunk.text for chunk in base_chunks])
        return self.semantic_chunker(combined_text)


class ChunkerClient:
    def __init__(self, chunker_config: ChunkerConfig, llm_client: OpenAIClient = None):
        self.chunker_config = chunker_config
        self.embedding_model = chunker_config.embedding_model
        self.chunk_size = chunker_config.chunk_size
        self.threshold = chunker_config.threshold
        self.language = chunker_config.language
        self.skip_window = chunker_config.skip_window
        self.min_sentences = chunker_config.min_sentences
        self.min_characters_per_chunk = chunker_config.min_characters_per_chunk
        self.llm_client = llm_client

        # 可选参数（从配置中安全获取，提供默认值）
        self.chunk_overlap = getattr(chunker_config, 'chunk_overlap', 0)
        self.min_sentences_per_chunk = getattr(chunker_config, 'min_sentences_per_chunk', 1)
        self.min_characters_per_sentence = getattr(chunker_config, 'min_characters_per_sentence', 12)
        self.delim = getattr(chunker_config, 'delim', [".", "!", "?", "\n"])
        self.include_delim = getattr(chunker_config, 'include_delim', "prev")
        self.tokenizer_or_token_counter = getattr(chunker_config, 'tokenizer_or_token_counter', "character")

        # 初始化具体分块器策略
        if chunker_config.chunker_strategy == "TokenChunker":
            self.chunker = TokenChunker(
                tokenizer=self.tokenizer_or_token_counter,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )
        elif chunker_config.chunker_strategy == "SemanticChunker":
            self.chunker = SemanticChunker(
                embedding_model=self.embedding_model,
                threshold=self.threshold,
                chunk_size=self.chunk_size,
                min_sentences=self.min_sentences,
            )
        elif chunker_config.chunker_strategy == "RecursiveChunker":
            self.chunker = RecursiveChunker(
                rules=RecursiveRules(),
                min_characters_per_chunk=self.min_characters_per_chunk or 50,
                chunk_size=self.chunk_size,
            )
        elif chunker_config.chunker_strategy == "LateChunker":
            self.chunker = LateChunker(
                embedding_model=self.embedding_model,
                chunk_size=self.chunk_size,
                rules=RecursiveRules(),
                min_characters_per_chunk=self.min_characters_per_chunk,
            )
        elif chunker_config.chunker_strategy == "NeuralChunker":
            self.chunker = NeuralChunker(
                model=self.embedding_model,
                min_characters_per_chunk=self.min_characters_per_chunk,
            )
        elif chunker_config.chunker_strategy == "LLMChunker":
            if not llm_client:
                raise ValueError("LLMChunker requires an LLM client")
            self.chunker = LLMChunker(llm_client, self.chunk_size)
        elif chunker_config.chunker_strategy == "HybridChunker":
            self.chunker = HybridChunker(
                semantic_threshold=self.threshold,
                base_chunk_size=self.chunk_size,
            )
        elif chunker_config.chunker_strategy == "SentenceChunker":
            self.chunker = SentenceChunker(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                min_sentences_per_chunk=self.min_sentences_per_chunk,
                min_characters_per_sentence=self.min_characters_per_sentence,
                delim=self.delim,
                include_delim=self.include_delim,
            )
        else:
            raise ValueError(f"Unknown chunker strategy: {chunker_config.chunker_strategy}")

    async def generate_chunks(self, dialogue: DialogData):
        """
        Generate chunks following 1 Message = 1 Chunk strategy.
        
        Each message creates one chunk, directly inheriting role information.
        If a message is too long, it will be split into multiple sub-chunks,
        each maintaining the same speaker.
        
        Raises:
            ValueError: If dialogue has no messages or chunking fails
        """
        # Validate dialogue has messages
        if not dialogue.context or not dialogue.context.msgs:
            raise ValueError(
                f"Dialogue {dialogue.ref_id} has no messages. "
                f"Cannot generate chunks from empty dialogue."
            )
        
        dialogue.chunks = []
        
        # 按消息分块：每个消息创建一个或多个 chunk，直接继承角色
        for msg_idx, msg in enumerate(dialogue.context.msgs):
            # Validate message has required attributes
            if not hasattr(msg, 'role') or not hasattr(msg, 'msg'):
                raise ValueError(
                    f"Message {msg_idx} in dialogue {dialogue.ref_id} "
                    f"missing 'role' or 'msg' attribute"
                )
            
            msg_content = msg.msg.strip()
            
            # Skip empty messages
            if not msg_content:
                continue
            
            # 如果消息太长，可以进一步分块
            if len(msg_content) > self.chunk_size:
                # 对单个消息的内容进行分块
                try:
                    sub_chunks = self.chunker(msg_content)
                except Exception as e:
                    raise ValueError(
                        f"Failed to chunk long message {msg_idx} in dialogue {dialogue.ref_id}: {e}"
                    )
                
                for idx, sub_chunk in enumerate(sub_chunks):
                    sub_chunk_text = sub_chunk.text if hasattr(sub_chunk, 'text') else str(sub_chunk)
                    sub_chunk_text = sub_chunk_text.strip()
                    
                    if len(sub_chunk_text) < (self.min_characters_per_chunk or 50):
                        continue
                    
                    chunk = Chunk(
                        content=f"{msg.role}: {sub_chunk_text}",
                        speaker=msg.role,  # 直接继承角色
                        metadata={
                            "message_index": msg_idx,
                            "message_role": msg.role,
                            "sub_chunk_index": idx,
                            "total_sub_chunks": len(sub_chunks),
                            "chunker_strategy": self.chunker_config.chunker_strategy,
                        },
                    )
                    dialogue.chunks.append(chunk)
            else:
                # 消息不长，直接作为一个 chunk
                chunk = Chunk(
                    content=f"{msg.role}: {msg_content}",
                    speaker=msg.role,  # 直接继承角色
                    metadata={
                        "message_index": msg_idx,
                        "message_role": msg.role,
                        "chunker_strategy": self.chunker_config.chunker_strategy,
                    },
                )
                dialogue.chunks.append(chunk)
        
        # Validate we generated at least one chunk
        if not dialogue.chunks:
            raise ValueError(
                f"No valid chunks generated for dialogue {dialogue.ref_id}. "
                f"All messages were either empty or too short. "
                f"Messages count: {len(dialogue.context.msgs)}"
            )
        
        return dialogue

    def evaluate_chunking(self, dialogue: DialogData) -> dict:
        """Evaluate chunking quality."""
        if not getattr(dialogue, 'chunks', None):
            return {}

        chunks = dialogue.chunks
        total_chars = sum(len(chunk.content) for chunk in chunks)
        avg_chunk_size = total_chars / len(chunks)

        # 计算各种指标
        chunk_sizes = [len(chunk.content) for chunk in chunks]

        metrics = {
            "strategy": self.chunker_config.chunker_strategy,
            "num_chunks": len(chunks),
            "total_characters": total_chars,
            "avg_chunk_size": avg_chunk_size,
            "min_chunk_size": min(chunk_sizes),
            "max_chunk_size": max(chunk_sizes),
            "chunk_size_std": np.std(chunk_sizes) if len(chunk_sizes) > 1 else 0,
            "coverage_ratio": total_chars / len(dialogue.content) if dialogue.content else 0,
        }

        return metrics

    def save_chunking_results(self, dialogue: DialogData, output_path: str):
        """Save chunking results to file with strategy name in filename."""
        strategy_name = self.chunker_config.chunker_strategy
        base_name, ext = os.path.splitext(output_path)
        strategy_output_path = f"{base_name}_{strategy_name}{ext}"

        with open(strategy_output_path, 'w', encoding='utf-8') as f:
            f.write(f"=== Chunking Strategy: {strategy_name} ===\n")
            f.write(f"Total chunks: {len(dialogue.chunks)}\n")
            f.write(f"Total characters: {sum(len(chunk.content) for chunk in dialogue.chunks)}\n")
            f.write("=" * 60 + "\n\n")

            for i, chunk in enumerate(dialogue.chunks):
                f.write(f"Chunk {i+1}:\n")
                f.write(f"Size: {len(chunk.content)} characters\n")
                if hasattr(chunk, 'metadata') and 'start_index' in chunk.metadata:
                    f.write(f"Position: {chunk.metadata.get('start_index')}-{chunk.metadata.get('end_index')}\n")
                f.write(f"Content: {chunk.content}\n")
                f.write("-" * 40 + "\n\n")

        print(f"Chunking results saved to: {strategy_output_path}")
        return strategy_output_path
