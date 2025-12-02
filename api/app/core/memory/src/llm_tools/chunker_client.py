from typing import Any, List
import re
import os
import asyncio
import json
import numpy as np

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
    from app.core.memory.src.llm_tools.openai_client import OpenAIClient
except Exception:
    # 在测试或无可用依赖（如 langfuse）环境下，允许惰性导入
    OpenAIClient = Any


class LLMChunker:
    """基于LLM的智能分块策略"""
    def __init__(self, llm_client: OpenAIClient, chunk_size: int = 1000):
        self.llm_client = llm_client
        self.chunk_size = chunk_size

    async def __call__(self, text: str) -> List[Any]:
        # 使用LLM分析文本结构并进行智能分块
        prompt = f"""
            请将以下文本分割成语义连贯的段落。每个段落应该围绕一个主题，长度大约在{self.chunk_size}字符左右。
            请以JSON格式返回结果，包含chunks数组，每个chunk有text字段。

            文本内容：
            {text[:5000]}
            """

        messages = [
            {"role": "system", "content": "你是一个专业的文本分析助手，擅长将长文本分割成语义连贯的段落。"},
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
            # 某些 chonkie 版本的 SentenceChunker 不支持 tokenizer_or_token_counter 参数
            # 为了兼容不同版本，这里仅传递广泛支持的参数
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
        生成分块，支持异步操作
        """
        try:
            # 预处理文本：确保对话标记格式统一
            content = dialogue.content
            content = content.replace('AI：', 'AI:').replace('用户：', '用户:')  # 统一冒号
            content = re.sub(r'(\n\s*)+\n', '\n\n', content)  # 合并多个空行

            if hasattr(self.chunker, '__call__') and not asyncio.iscoroutinefunction(self.chunker.__call__):
                # 同步分块器
                chunks = self.chunker(content)
            else:
                # 异步分块器（如LLMChunker）
                chunks = await self.chunker(content)

            # 过滤空块和过小的块
            valid_chunks = []
            for c in chunks:
                chunk_text = getattr(c, 'text', str(c)) if not isinstance(c, str) else c
                if isinstance(chunk_text, str) and len(chunk_text.strip()) >= (self.min_characters_per_chunk or 50):
                    valid_chunks.append(c)

            dialogue.chunks = [
                Chunk(
                    content=c.text if hasattr(c, 'text') else str(c),
                    metadata={
                        "start_index": getattr(c, "start_index", None),
                        "end_index": getattr(c, "end_index", None),
                        "chunker_strategy": self.chunker_config.chunker_strategy,
                    },
                )
                for c in valid_chunks
            ]
            return dialogue

        except Exception as e:
            print(f"分块失败: {e}")

            # 改进的后备方案：尝试按对话回合分割
            try:
                # 简单的按对话分割
                dialogue_pattern = r'(AI:|用户:)(.*?)(?=AI:|用户:|$)'
                matches = re.findall(dialogue_pattern, dialogue.content, re.DOTALL)

                class SimpleChunk:
                    def __init__(self, text, start_index, end_index):
                        self.text = text
                        self.start_index = start_index
                        self.end_index = end_index

                chunks = []
                current_chunk = ""
                current_start = 0

                for match in matches:
                    speaker, ct = match[0], match[1].strip()
                    turn_text = f"{speaker} {ct}"

                    if len(current_chunk) + len(turn_text) > (self.chunk_size or 500):
                        if current_chunk:
                            chunks.append(SimpleChunk(current_chunk, current_start, current_start + len(current_chunk)))
                        current_chunk = turn_text
                        current_start = dialogue.content.find(turn_text, current_start)
                    else:
                        current_chunk += ("\n" + turn_text) if current_chunk else turn_text

                if current_chunk:
                    chunks.append(SimpleChunk(current_chunk, current_start, current_start + len(current_chunk)))

                dialogue.chunks = [
                    Chunk(
                        content=c.text,
                        metadata={
                            "start_index": c.start_index,
                            "end_index": c.end_index,
                            "chunker_strategy": "DialogueTurnFallback",
                        },
                    )
                    for c in chunks
                ]

            except Exception:
                # 最后的手段：单一大块
                dialogue.chunks = [Chunk(
                    content=dialogue.content,
                    metadata={"chunker_strategy": "SingleChunkFallback"},
                )]

            return dialogue

    def evaluate_chunking(self, dialogue: DialogData) -> dict:
        """
        评估分块质量
        """
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
        """
        保存分块结果到文件，文件名包含策略名称
        """
        strategy_name = self.chunker_config.chunker_strategy
        # 在文件名中添加策略名称
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
