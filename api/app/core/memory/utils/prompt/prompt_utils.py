import os
from jinja2 import Environment, FileSystemLoader

from app.core.memory.utils.log.logging_utils import log_prompt_rendering, log_template_rendering

# Setup Jinja2 environment
# Get the directory of this file (app/core/memory/utils/prompt/)
current_dir = os.path.dirname(os.path.abspath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")
prompt_env = Environment(loader=FileSystemLoader(prompt_dir))

async def get_prompts(message: str) -> list[dict]:
    """
    Renders system and user prompts using Jinja2 templates.
    """
    system_template = prompt_env.get_template("system.jinja2")
    user_template = prompt_env.get_template("user.jinja2")

    system_prompt = system_template.render()
    user_prompt = user_template.render(message=message)

    # 记录渲染结果到提示日志（与示例日志结构一致）
    log_prompt_rendering('system', system_prompt)
    log_prompt_rendering('user', user_prompt)
    # 可选：记录模板渲染信息（仅当 prompt_templates.log 存在时生效）
    log_template_rendering('system.jinja2', {})
    log_template_rendering('user.jinja2', {'message': message})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

async def render_statement_extraction_prompt(
    chunk_content: str,
    definitions: dict,
    json_schema: dict,
    granularity: int | None = None,
    include_dialogue_context: bool = False,
    dialogue_content: str | None = None,
    max_dialogue_chars: int | None = None,
) -> str:
    """
    Renders the statement extraction prompt using the extract_statement.jinja2 template.

    Args:
        chunk_content: The content of the chunk to process
        definitions: Label definitions for statement classification
        json_schema: JSON schema for the expected output format

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("extract_statement.jinja2")
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
        definitions=definitions,
        json_schema=json_schema,
        granularity=granularity,
        include_dialogue_context=include_dialogue_context,
        dialogue_context=ctx,
    )
    # 记录渲染结果到提示日志（与示例日志结构一致）
    log_prompt_rendering('statement extraction', rendered_prompt)
    # 可选：记录模板渲染信息
    log_template_rendering('extract_statement.jinja2', {
        'inputs': 'chunk',
        'definitions': 'LABEL_DEFINITIONS',
        'json_schema': 'StatementExtractionResponse.schema',
        'granularity': 'int|None',
        'include_dialogue_context': include_dialogue_context,
        'dialogue_context_len': (len(ctx) if isinstance(ctx, str) else 0),
    })

    return rendered_prompt

async def render_temporal_extraction_prompt(
    ref_dates: dict,
    statement: dict,
    temporal_guide: dict,
    statement_guide: dict,
    json_schema: dict,
) -> str:
    """
    Renders the temporal extraction prompt using the extract_temporal.jinja2 template.

    Args:
        ref_dates: Reference dates for context.
        statement: The statement to process.
        temporal_guide: Guidance on temporal types.
        statement_guide: Guidance on statement types.
        json_schema: JSON schema for the expected output format.

    Returns:
        Rendered prompt content as a string.
    """
    template = prompt_env.get_template("extract_temporal.jinja2")
    inputs = ref_dates | statement
    rendered_prompt = template.render(
        inputs=inputs,
        temporal_guide=temporal_guide,
        statement_guide=statement_guide,
        json_schema=json_schema,
    )
    # 记录渲染结果到提示日志（与示例日志结构一致）
    log_prompt_rendering('temporal extraction', rendered_prompt)
    # 可选：记录模板渲染信息
    log_template_rendering('extract_temporal.jinja2', {
        'inputs': 'ref_dates|statement',
        'temporal_guide': 'dict',
        'statement_guide': 'dict',
        'json_schema': 'Temporal.schema'
    })

    return rendered_prompt

def render_entity_dedup_prompt(
    entity_a: dict,
    entity_b: dict,
    context: dict,
    json_schema: dict,
    disambiguation_mode: bool = False,
) -> str:
    """
    Render the entity deduplication prompt using the entity_dedup.jinja2 template.

    Args:
        entity_a: Dict of entity A attributes
        entity_b: Dict of entity B attributes
        context: Dict of computed signals (group/type gate, similarities, co-occurrence, relation statements)
        json_schema: JSON schema for the structured output (EntityDedupDecision)

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
    )

    # prompt_logger.info("\n=== RENDERED ENTITY DEDUP PROMPT ===")
    # prompt_logger.info(rendered_prompt)
    # prompt_logger.info("\n" + "="*50 + "\n")

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
async def render_triplet_extraction_prompt(statement: str, chunk_content: str, json_schema: dict, predicate_instructions: dict = None) -> str:
    """
    Renders the triplet extraction prompt using the extract_triplet.jinja2 template.

    Args:
        statement: Statement text to process
        chunk_content: The content of the chunk to process
        json_schema: JSON schema for the expected output format
        predicate_instructions: Optional predicate instructions

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("extract_triplet.jinja2")
    rendered_prompt = template.render(
        statement=statement,
        chunk_content=chunk_content,
        json_schema=json_schema,
        predicate_instructions=predicate_instructions
    )
    # 记录渲染结果到提示日志（与示例日志结构一致）
    log_prompt_rendering('triplet extraction', rendered_prompt)
    # 可选：记录模板渲染信息
    log_template_rendering('extract_triplet.jinja2', {
        'statement': 'str',
        'chunk_content': 'str',
        'json_schema': 'TripletExtractionResponse.schema',
        'predicate_instructions': 'PREDICATE_DEFINITIONS'
    })

    return rendered_prompt

async def render_memory_summary_prompt(
    chunk_texts: str,
    json_schema: dict,
    max_words: int = 200,
) -> str:
    """
    Renders the memory summary prompt using the memory_summary.jinja2 template.

    Args:
        chunk_texts: Concatenated text of conversation chunks
        json_schema: JSON schema for the expected output format
        max_words: Maximum words for the summary

    Returns:
        Rendered prompt content as string.
    """
    template = prompt_env.get_template("memory_summary.jinja2")
    rendered_prompt = template.render(
        chunk_texts=chunk_texts,
        json_schema=json_schema,
        max_words=max_words,
    )
    log_prompt_rendering('memory summary', rendered_prompt)
    log_template_rendering('memory_summary.jinja2', {
        'chunk_texts_len': len(chunk_texts or ""),
        'max_words': max_words,
        'json_schema': 'MemorySummaryResponse.schema'
    })
    return rendered_prompt

async def render_emotion_extraction_prompt(
    statement: str,
    extract_keywords: bool,
    enable_subject: bool
) -> str:
    """
    Renders the emotion extraction prompt using the extract_emotion.jinja2 template.

    Args:
        statement: The statement to analyze
        extract_keywords: Whether to extract emotion keywords
        enable_subject: Whether to enable subject classification

    Returns:
        Rendered prompt content as string
    """
    template = prompt_env.get_template("extract_emotion.jinja2")
    rendered_prompt = template.render(
        statement=statement,
        extract_keywords=extract_keywords,
        enable_subject=enable_subject
    )
    
    # 记录渲染结果到提示日志
    log_prompt_rendering('emotion extraction', rendered_prompt)
    # 可选：记录模板渲染信息
    log_template_rendering('extract_emotion.jinja2', {
        'statement': 'str',
        'extract_keywords': extract_keywords,
        'enable_subject': enable_subject
    })
    
    return rendered_prompt

async def render_emotion_suggestions_prompt(
    health_data: dict,
    patterns: dict,
    user_profile: dict
) -> str:
    """
    Renders the emotion suggestions generation prompt using the generate_emotion_suggestions.jinja2 template.

    Args:
        health_data: 情绪健康数据
        patterns: 情绪模式分析结果
        user_profile: 用户画像数据

    Returns:
        Rendered prompt content as string
    """
    import json
    
    # 预处理 emotion_distribution 为 JSON 字符串
    emotion_distribution_json = json.dumps(
        health_data.get('emotion_distribution', {}), 
        ensure_ascii=False, 
        indent=2
    )
    
    template = prompt_env.get_template("generate_emotion_suggestions.jinja2")
    rendered_prompt = template.render(
        health_data=health_data,
        patterns=patterns,
        user_profile=user_profile,
        emotion_distribution_json=emotion_distribution_json
    )
    
    # 记录渲染结果到提示日志
    log_prompt_rendering('emotion suggestions', rendered_prompt)
    # 可选：记录模板渲染信息
    log_template_rendering('generate_emotion_suggestions.jinja2', {
        'health_score': health_data.get('health_score'),
        'health_level': health_data.get('level'),
        'user_interests': user_profile.get('interests', [])
    })
    
    return rendered_prompt
