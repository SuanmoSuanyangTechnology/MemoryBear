"""完整处理冻结的 Wiki v1 内容，不借用旧 QA 的静默截断路径。"""

from ..chunk.token_utils import split_by_token_limit
from ..wiki.config import WikiError
from ..wiki.model import request_tokens
from .contracts import FAQ_PROMPT_VERSION, GeneratedFAQs

# JSON escaping/段号等传输开销；每个窗口还会用实际 payload 再校验。
PAYLOAD_TOKEN_RESERVE = 256


def generate(model, snapshot):
    context = {"title": snapshot["title"], "language": snapshot["language"]}
    overhead = request_tokens(FAQ_PROMPT_VERSION, {**context, "text": ""}, GeneratedFAQs)
    budget = min(
        model.settings.kb_wiki_segment_tokens,
        model.settings.kb_wiki_input_tokens - overhead - PAYLOAD_TOKEN_RESERVE,
    )
    if budget <= 0:
        raise WikiError(
            "WIKI_CONTEXT_BUDGET_EXCEEDED",
            retryable=False,
            diagnostics={"stage": FAQ_PROMPT_VERSION, "overhead_tokens": overhead},
        )
    text = snapshot["content"]
    segments = split_by_token_limit(text, budget, disallowed_special=())
    # JSON escaping 可能令 payload 超预算；继续对当前窗口拆分，不丢弃任何内容。
    windows = []
    pending = list(reversed(segments))
    while pending:
        part = pending.pop()
        if request_tokens(FAQ_PROMPT_VERSION, {**context, "text": part}, GeneratedFAQs) > (
            model.settings.kb_wiki_input_tokens
        ):
            if len(part) <= 1:
                raise WikiError("WIKI_CONTEXT_BUDGET_EXCEEDED", retryable=False)
            middle = len(part) // 2
            pending.extend([part[middle:], part[:middle]])
        else:
            windows.append(part)
        if len(windows) + len(pending) > model.settings.kb_wiki_max_segments:
            raise WikiError(
                "WIKI_FAQ_SEGMENT_LIMIT",
                retryable=False,
                diagnostics={"stage": "faq_segment", "size": len(windows) + len(pending)},
            )
    seen, output = set(), []
    for part in windows:
        result = model.call(FAQ_PROMPT_VERSION, {**context, "text": part}, GeneratedFAQs)
        for item in result["items"]:
            identity = (item["standard_question"], item["standard_answer"])
            if identity not in seen:
                seen.add(identity)
                output.append(item)
    return output
