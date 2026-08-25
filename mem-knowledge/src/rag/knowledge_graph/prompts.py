"""Evidence Graph extraction prompts."""

import json

EXTRACTION_SYSTEM_PROMPT = """
You extract traceable entities and direct relations from source chunks.
Return only data explicitly supported by the input. Give every entity a unique
ref within this response. Relation endpoints must reference entity refs emitted
in the same response. Each relation should include concise high-level keywords
explicitly supported by the source text. Omit uncertain facts. Return a valid
JSON object containing "entities" and "relations" arrays and no other text.
""".strip()

EXTRACTION_PROMPT_VERSION = "2026-07-27-v1"


def build_extraction_prompt(
    source_text: str,
    entity_types: tuple[str, ...],
    scene_name: str,
) -> str:
    allowed_types = json.dumps(list(entity_types), ensure_ascii=False, separators=(",", ":"))
    scene_instruction = f"Domain context: {scene_name.strip()}\n" if scene_name.strip() else ""
    return (
        f"{scene_instruction}Allowed entity types: {allowed_types}\n"
        "Extract only direct evidence from the following source chunk.\n\n"
        f"{source_text}"
    )


__all__ = ["EXTRACTION_PROMPT_VERSION", "EXTRACTION_SYSTEM_PROMPT", "build_extraction_prompt"]
