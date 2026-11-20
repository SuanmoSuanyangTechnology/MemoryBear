import json
import os
from typing import Any as OntologyTypeList

from jinja2 import Environment, FileSystemLoader

from src.common.observability.logging import get_logger

prompt_logger = get_logger("memory.prompts")
template_logger = get_logger("memory.templates")

# Setup Jinja2 environment
# Resolve templates from src/prompt/extraction_engine/.
current_dir = os.path.dirname(os.path.abspath(__file__))
prompt_dir = os.path.join(os.path.dirname(os.path.dirname(current_dir)), "prompt", "extraction_engine")
prompt_env = Environment(loader=FileSystemLoader(prompt_dir))


async def render_statement_extraction_prompt(
    chunk_content: str,
    definitions: dict,
    json_schema: dict,
    granularity: int | None = None,
    include_dialogue_context: bool = False,
    dialogue_content: str | None = None,
    max_dialogue_chars: int | None = None,
    language: str = "zh",
    input_json: dict | None = None,
) -> str:
    """
    Renders the statement extraction prompt using the extract_statement.jinja2 template.

    Args:
        chunk_content: The content of the chunk to process
        definitions: Label definitions for statement classification
        json_schema: JSON schema for the expected output format
        granularity: Extraction granularity level (1-3)
        include_dialogue_context: Whether to include full dialogue context
        dialogue_content: Full dialogue content for context
        max_dialogue_chars: Maximum characters for dialogue context
        language: Language for output ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("extract_statement_temporal.jinja2")
    # Optional clipping of dialogue context
    ctx = None
    if include_dialogue_context and dialogue_content:
        try:
            if isinstance(max_dialogue_chars, int) and max_dialogue_chars > 0:
                ctx = dialogue_content[:max_dialogue_chars]
            else:
                ctx = dialogue_content
        except Exception:
            ctx = dialogue_content

    rendered_prompt = template.render(
        inputs={"chunk": chunk_content},
        input_json=json.dumps(input_json, ensure_ascii=False) if input_json else "{}",
        definitions=definitions,
        json_schema=json_schema,
        granularity=granularity,
        include_dialogue_context=include_dialogue_context,
        dialogue_context=ctx,
        language=language,
    )
    # 记录渲染结果到提示日志（与示例日志结构一致）
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("statement extraction").upper(), rendered_prompt, "=" * 50)
    # 可选：记录模板渲染信息
    template_logger.debug("Rendering template: %s with context keys: %s", "extract_statement_temporal.jinja2", list(({
            "inputs": "chunk",
            "definitions": "LABEL_DEFINITIONS",
            "json_schema": "StatementExtractionResponse.schema",
            "granularity": "int|None",
            "include_dialogue_context": include_dialogue_context,
            "dialogue_context_len": (len(ctx) if isinstance(ctx, str) else 0),
        }).keys()))

    return rendered_prompt


async def render_statement_classification_fallback_prompt(
    statement_text: str,
    language: str = "zh",
) -> str:
    """渲染备用 LLM 三字段分类 Prompt（statement 拆分阶段 2 兜底）。

    小模型不可用/非法返回时，由大模型为单条 statement 补
    statement_type / temporal_type / has_unsolved_reference 三个字段。
    """
    template = prompt_env.get_template("statement_classification_fallback.jinja2")
    input_json = {"statement_text": statement_text}
    rendered_prompt = template.render(
        input_json=json.dumps(input_json, ensure_ascii=False),
        language=language,
    )
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("statement classification fallback").upper(), rendered_prompt, "=" * 50)
    return rendered_prompt


# async def render_entity_dedup_prompt(
#     entity_a: dict,
#     entity_b: dict,
#     context: dict,
#     json_schema: dict,
# ) -> str:
#     """
#     Render the entity deduplication prompt using the entity_dedup.jinja2 template.

#     Args:
#         entity_a: Dict of entity A attributes


async def render_triplet_extraction_prompt(
    statement: str,
    chunk_content: str,
    json_schema: dict,
    predicate_instructions: dict = None,
    language: str = "zh",
    ontology_types: "OntologyTypeList | None" = None,
    speaker: str = None,
    input_json: dict = None,
    has_unsolved_reference: bool = False,
) -> str:
    """
    Renders the triplet extraction prompt using the extract_triplet.jinja2 template.

    Args:
        statement: Statement text to process
        chunk_content: The content of the chunk to process
        json_schema: JSON schema for the expected output format
        predicate_instructions: Optional predicate instructions
        language: The language to use for entity descriptions ("zh" for Chinese, "en" for English)
        ontology_types: Optional OntologyTypeList containing predefined ontology types for entity classification
        speaker: Speaker role ("user" or "assistant") for the current statement
        input_json: Full input JSON for the template
        has_unsolved_reference: Whether the statement has unresolved references

    Returns:
        Rendered prompt content as string
    """
    import json

    template = prompt_env.get_template("extract_triplet.jinja2")

    # 准备本体类型数据
    ontology_type_section = None
    ontology_type_names = []
    type_hierarchy_hints = []
    if ontology_types and ontology_types.types:
        ontology_type_section = ontology_types.to_prompt_section()
        ontology_type_names = ontology_types.get_type_names()
        type_hierarchy_hints = ontology_types.get_type_hierarchy_hints()

    # 准备 input_json 如果没有提供
    if input_json is None:
        input_json = {}

    rendered_prompt = template.render(
        statement=statement,
        statement_text=statement,  # 兼容模板中的 statement_text 变量
        chunk_content=chunk_content,
        json_schema=json_schema,
        predicate_instructions=predicate_instructions,
        language=language,
        ontology_types=ontology_type_section,
        ontology_type_names=ontology_type_names,
        type_hierarchy_hints=type_hierarchy_hints,
        speaker=speaker,
        input_json=json.dumps(input_json, ensure_ascii=False) if input_json else "{}",
        has_unsolved_reference=has_unsolved_reference,
    )
    # 记录渲染结果到提示日志（与示例日志结构一致）
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("triplet extraction").upper(), rendered_prompt, "=" * 50)
    # 可选：记录模板渲染信息
    template_logger.debug("Rendering template: %s with context keys: %s", "extract_triplet.jinja2", list(({
            "statement": "str",
            "chunk_content": "str",
            "json_schema": "TripletExtractionResponse.schema",
            "predicate_instructions": "PREDICATE_DEFINITIONS",
            "language": language,
            "ontology_types": bool(ontology_type_section),
            "ontology_type_count": len(ontology_type_names),
            "type_hierarchy_hints_count": len(type_hierarchy_hints),
        }).keys()))

    return rendered_prompt


def render_entity_dedup_prompt(
    entity_a: dict,
    entity_b: dict,
    context: dict,
    json_schema: dict,
    disambiguation_mode: bool = False,
    language: str = "zh",
) -> str:
    """
    Render the entity deduplication prompt using the entity_dedup.jinja2 template.

    Args:
        entity_a: Dict of entity A attributes
        entity_b: Dict of entity B attributes
        context: Dict of computed signals (group/type gate, similarities, co-occurrence, relation statements)
        json_schema: JSON schema for the structured output (EntityDedupDecision)
        disambiguation_mode: Whether to use disambiguation mode
        language: Language for output ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("entity_dedup.jinja2")
    rendered_prompt = template.render(
        entity_a=entity_a,
        entity_b=entity_b,
        same_group=context.get("same_group", False),
        type_ok=context.get("type_ok", False),
        type_similarity=context.get("type_similarity", 0.0),
        name_text_sim=context.get("name_text_sim", 0.0),
        name_embed_sim=context.get("name_embed_sim", 0.0),
        name_contains=context.get("name_contains", False),
        co_occurrence=context.get("co_occurrence", False),
        relation_statements=context.get("relation_statements", []),
        json_schema=json_schema,
        disambiguation_mode=disambiguation_mode,
        language=language,
    )

    # prompt_logger.info("\n=== RENDERED ENTITY DEDUP PROMPT ===")
    # prompt_logger.info(rendered_prompt)
    # prompt_logger.info("\n" + "="*50 + "\n")

    return rendered_prompt


async def render_memory_summary_prompt(  # 只提供给RAG存储使用
    chunk_texts: str,
    json_schema: dict,
    max_words: int = 200,
    language: str = "zh",
) -> str:
    """
    Renders the memory summary prompt using the memory_summary.jinja2 template.

    Args:
        chunk_texts: Concatenated text of conversation chunks
        json_schema: JSON schema for the expected output format
        max_words: Maximum words for the summary
        language: The language to use for summary generation ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string.
    """
    template = prompt_env.get_template("memory_summary.jinja2")
    rendered_prompt = template.render(
        chunk_texts=chunk_texts,
        json_schema=json_schema,
        max_words=max_words,
        language=language,
    )
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("memory summary").upper(), rendered_prompt, "=" * 50)
    template_logger.debug("Rendering template: %s with context keys: %s", "memory_summary.jinja2", list(({
            "chunk_texts_len": len(chunk_texts or ""),
            "max_words": max_words,
            "json_schema": "MemorySummaryResponse.schema",
            "language": language,
        }).keys()))
    return rendered_prompt


async def render_emotion_extraction_prompt(
    statement: str, extract_keywords: bool, enable_subject: bool, language: str = "zh"
) -> str:
    """
    [DEPRECATED v0.5.2] Statement LLM 情绪提取已废弃，改为继承快写 BERT 情绪（见 emotion_inheritance），暂时保留代码不删除

    Renders the emotion extraction prompt using the extract_emotion.jinja2 template.

    Args:
        statement: The statement to analyze
        extract_keywords: Whether to extract emotion keywords
        enable_subject: Whether to enable subject classification
        language: Language for output ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("extract_emotion.jinja2")
    rendered_prompt = template.render(
        statement=statement,
        extract_keywords=extract_keywords,
        enable_subject=enable_subject,
        language=language,
    )

    # 记录渲染结果到提示日志
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("emotion extraction").upper(), rendered_prompt, "=" * 50)
    # 可选：记录模板渲染信息
    template_logger.debug("Rendering template: %s with context keys: %s", "extract_emotion.jinja2", list(({
            "statement": "str",
            "extract_keywords": extract_keywords,
            "enable_subject": enable_subject,
        }).keys()))

    return rendered_prompt


async def render_episodic_title_and_type_prompt(content: str, language: str = "zh") -> str:
    """
    Renders the episodic title and type classification prompt using the episodic_type_classification.jinja2 template.

    Args:
        content: The content of the episodic memory summary to analyze
        language: The language to use for title generation ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("episodic_type_classification.jinja2")
    rendered_prompt = template.render(content=content, language=language)

    # 记录渲染结果到提示日志
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("episodic title and type classification").upper(), rendered_prompt, "=" * 50)
    # 可选：记录模板渲染信息
    template_logger.debug("Rendering template: %s with context keys: %s", "episodic_type_classification.jinja2", list(({"content_len": len(content) if content else 0, "language": language}).keys()))

    return rendered_prompt




async def render_neo4j_memory_summary_prompt(
    chunk_texts: str,
    max_words: int = 200,
    language: str = "zh",
) -> str:
    """Render the combined neo4j memory title + type + summary prompt.

    Uses neo4j_memory_summary.jinja2 to generate title, type,
    and summary in a single LLM call, reducing per-chunk calls from 2 to 1.

    Args:
        chunk_texts: The content of the chunk to summarize
        max_words: Maximum words for the summary
        language: Language for output ("zh" for Chinese, "en" for English)

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("neo4j_memory_summary.jinja2")
    rendered_prompt = template.render(
        chunk_texts=chunk_texts,
        max_words=max_words,
        language=language,
    )
    prompt_logger.info("\n=== RENDERED %s PROMPT ===\n%s\n%s\n", ("neo4j memory summary (title+type+summary)").upper(), rendered_prompt, "=" * 50)
    template_logger.debug("Rendering template: %s with context keys: %s", "neo4j_memory_summary.jinja2", list(({
            "chunk_texts_len": len(chunk_texts or ""),
            "max_words": max_words,
            "language": language,
        }).keys()))
    return rendered_prompt
