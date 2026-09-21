"""Lightweight helpers shared by vision model adapters."""
import os
import re
from functools import lru_cache
from pathlib import Path

import jinja2


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    base = os.getenv("RAG_PROJECT_BASE") or os.getenv("RAG_DEPLOY_BASE") or str(Path(__file__).resolve().parents[3])
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(base) / "res"))
    return tiktoken.get_encoding("cl100k_base")


@lru_cache(maxsize=1)
def _vision_template():
    prompt = Path(__file__).with_name("vision_llm_describe_prompt.md").read_text(encoding="utf-8").strip()
    return jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True).from_string(prompt)


def vision_llm_describe_prompt(page=None, lang="Chinese") -> str:
    return _vision_template().render(page=page, lang=lang)


def is_english(texts):
    if not texts:
        return False

    pattern = re.compile(r"[`a-zA-Z0-9\s.,':;/\"?<>!\(\)\-]")

    if isinstance(texts, str):
        texts = list(texts)
    elif isinstance(texts, list):
        texts = [t for t in texts if isinstance(t, str) and t.strip()]
    else:
        return False

    if not texts:
        return False

    eng = sum(1 for t in texts if pattern.fullmatch(t.strip()))
    return (eng / len(texts)) > 0.8

def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    try:
        code_list = _encoder().encode(string)
        return len(code_list)
    except Exception:
        return 0

def total_token_count_from_response(resp):
    if resp is None:
        return 0

    if hasattr(resp, "usage") and hasattr(resp.usage, "total_tokens"):
        try:
            return resp.usage.total_tokens
        except Exception:
            pass

    if hasattr(resp, "usage_metadata") and hasattr(resp.usage_metadata, "total_tokens"):
        try:
            return resp.usage_metadata.total_tokens
        except Exception:
            pass

    if 'usage' in resp and 'total_tokens' in resp['usage']:
        try:
            return resp["usage"]["total_tokens"]
        except Exception:
            pass

    if 'usage' in resp and 'input_tokens' in resp['usage'] and 'output_tokens' in resp['usage']:
        try:
            return resp["usage"]["input_tokens"] + resp["usage"]["output_tokens"]
        except Exception:
            pass

    if 'meta' in resp and 'tokens' in resp['meta'] and 'input_tokens' in resp['meta']['tokens'] and 'output_tokens' in resp['meta']['tokens']:
        try:
            return resp["meta"]["tokens"]["input_tokens"] + resp["meta"]["tokens"]["output_tokens"]
        except Exception:
            pass
    return 0
