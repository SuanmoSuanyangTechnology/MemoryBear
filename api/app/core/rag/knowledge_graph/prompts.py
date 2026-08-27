import json


EXTRACTION_SYSTEM_PROMPT = """
You extract traceable entities and direct relations from source chunks.
Return only data explicitly supported by the input. Give every entity a unique
ref within this response. Relation endpoints must reference
entity refs emitted in the same response. Each relation should include concise
high-level keywords explicitly supported by the source text. Omit uncertain facts. Do not answer
the user, build communities, summarize a whole graph, or infer missing facts.
Return a valid JSON object only. The JSON object must contain "entities" and
"relations" arrays. Use empty arrays when no direct evidence is present. Do not
return markdown, code fences, comments, or explanatory text.
Use exactly the field names below; never substitute alternatives such as
"type", "source", or "target".
Each entity: "ref" (string, unique within this response, e.g. "e1"), "name"
(string), "entity_type" (string, one of the allowed types), "description"
(string, one or two sentences grounded in the source). Optional: "aliases"
(string array), "confidence" (number 0-1).
Each relation: "from_ref" (string entity ref), "to_ref" (string entity ref),
"predicate" (string, short verb phrase), "description" (string, one sentence
grounded in the source). Optional: "keywords" (string array), "directed"
(boolean), "confidence" (number 0-1).
Example:
{
  "entities": [
    {
      "ref": "e1",
      "name": "Acme Corp",
      "entity_type": "organization",
      "description": "Semiconductor company headquartered in Hsinchu."
    },
    {
      "ref": "e2",
      "name": "Zeta",
      "entity_type": "product",
      "description": "AI accelerator chip launched by Acme Corp in 2025."
    }
  ],
  "relations": [
    {
      "from_ref": "e1",
      "to_ref": "e2",
      "predicate": "launched",
      "description": "Acme Corp launched the Zeta chip in 2025.",
      "keywords": ["product launch"]
    }
  ]
}
""".strip()

EXTRACTION_PROMPT_VERSION = "2026-07-27-v1"

QUERY_PLAN_PROMPT_VERSION = "2026-07-23-v2"


QUERY_ANALYSIS_SYSTEM_PROMPT = """
Build a retrieval plan from the user's query. Return only two JSON arrays:
low_level_keywords for concrete entities, proper names, attributes, and details;
high_level_keywords for themes, concepts, and relation intent.

Every keyword must be explicitly derived only from the query. Preserve meaningful phrases and
never invent unsupported entities, products, organizations, dates, or technical
terms. Prefer concise, meaningful phrases over isolated fragments. Keep both
lists short and high-signal, without duplicates. For simple, vague, or
nonsensical queries, return two empty arrays. Do not force both arrays to be
non-empty and do not answer the query.
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
        "Extract only direct evidence from the following source chunk.\n\n"
        f"{source_text}"
    )


def build_query_analysis_prompt(query: str) -> str:
    return (
        "Return a source-grounded low/high keyword plan for this query.\n"
        f"Query:\n{query.strip()}"
    )
