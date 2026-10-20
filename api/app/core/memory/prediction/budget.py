from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    context_tokens: int
    tier: str
    persona_desc_chars: int
    persona_statements: int
    persona_relations: int
    agency_batch_size: int
    max_candidates: int
    recall_top_k: int
    recall_render_chars: int
    timeline_items: int
    events_brief: int
    reactors_per_turn: int
    gate_samples_per_term: int
    grounding_pool: int
    action_tokens: int
    card_tokens: int
    agency_tokens: int
    gate_tokens: int
    report_tokens: int
    terms_tokens: int
    stop_tokens: int


def limits_for(context_tokens: int) -> Limits:
    if context_tokens <= 8_000:
        return Limits(context_tokens, "small", 900, 14, 8, 10, 60, 12, 170, 8, 8, 2, 3, 40, 700, 900, 900, 650, 1300, 350, 220)
    if context_tokens <= 32_000:
        return Limits(context_tokens, "medium", 1800, 24, 14, 15, 90, 16, 240, 12, 8, 2, 4, 70, 900, 1300, 1200, 800, 1800, 450, 260)
    return Limits(context_tokens, "large", 3000, 36, 20, 20, 120, 20, 320, 16, 8, 3, 5, 100, 1100, 1800, 1600, 1000, 2400, 550, 320)


DEFAULT_LIMITS = limits_for(128_000)
