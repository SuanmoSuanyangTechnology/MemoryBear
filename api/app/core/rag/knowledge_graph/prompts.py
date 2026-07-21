import json


EXTRACTION_SYSTEM_PROMPT = """
You extract traceable entities and direct relations from source chunks.
Return only data explicitly supported by the input. Every entity and relation
must cite one or more source_chunk_ids from the provided markers. Give every
entity a unique ref within this response. Relation endpoints must reference
entity refs emitted in the same response. Omit uncertain facts. Do not answer
the user, build communities, summarize a whole graph, or infer missing facts.
""".strip()


QUERY_ANALYSIS_SYSTEM_PROMPT = """
Split the query into entity-oriented terms and relation-oriented terms.
Keep terms short and preserve proper names. Do not answer the query.
""".strip()


def build_extraction_prompt(
    source_text: str,
    entity_types: tuple[str, ...],
    scene_name: str,
) -> str:
    allowed_types = json.dumps(
        list(entity_types),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    scene_instruction = (
        f"Domain context: {scene_name.strip()}\n"
        if scene_name.strip()
        else ""
    )
    return (
        f"{scene_instruction}"
        f"Allowed entity types: {allowed_types}\n"
        "Extract only direct evidence from the following marked chunks. "
        "Copy marker ids exactly into source_chunk_ids.\n\n"
        f"{source_text}"
    )


def build_query_analysis_prompt(query: str) -> str:
    return f"Query:\n{query.strip()}"
