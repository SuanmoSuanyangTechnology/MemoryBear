import json


def escape_lucene_query(query: str) -> str:
    """Escape Lucene special characters in a free-text query.

    This prevents ParseException when using Neo4j full-text procedures.
    """
    if query is None:
        return ""

    s = str(query)
    # Normalize whitespace
    s = s.replace("\r", " ").replace("\n", " ").strip()

    # Lucene reserved tokens/special characters
    specials = ['&&', '||', '\\', '+', '-', '!', '(', ')', '{', '}', '[', ']', '^', '"', '~', '*', '?', ':']
    # Replace longer tokens first to avoid partial double-escaping
    for token in sorted(specials, key=len, reverse=True):
        s = s.replace(token, f"\\{token}")

    return s

def extract_plain_query(query_input: str) -> str:
    """Extract clean, plain-text query from various input forms.

    - Strips surrounding quotes and whitespace
    - If input looks like JSON, prefers the 'original' field
    - Fallbacks to the raw string when parsing fails
    """
    if query_input is None:
        return ""

    # Directly handle dict-like input
    if isinstance(query_input, dict):
        original = query_input.get("original")
        if isinstance(original, str) and original.strip():
            return original.strip()
        context = query_input.get("context")
        if isinstance(context, dict):
            for key, val in context.items():
                if isinstance(key, str) and key.strip():
                    return key.strip()
                if isinstance(val, list) and val:
                    first = val[0]
                    if isinstance(first, str) and first.strip():
                        return first.strip()
        # Fallback to string conversion below

    s = str(query_input).strip()

    # Remove surrounding single/double quotes if present
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        s = s[1:-1].strip()

    # Attempt to parse JSON and extract the 'original' field
    if s.startswith("{") and s.endswith("}"):
        try:
            data = json.loads(s)
            # Prefer 'original' field if available
            original = data.get("original")
            if isinstance(original, str) and original.strip():
                return original.strip()
            # Fallbacks: try common nested structures
            context = data.get("context")
            if isinstance(context, dict):
                # Take the first key or first string value in context
                for key, val in context.items():
                    if isinstance(key, str) and key.strip():
                        return key.strip()
                    if isinstance(val, list) and val:
                        first = val[0]
                        if isinstance(first, str) and first.strip():
                            return first.strip()
        except Exception:
            # Not valid JSON; keep as-is after best-effort unescape below
            pass

    # Best-effort unescape common escaped newlines/tabs without altering unicode
    s = s.replace("\\n", " ").replace("\\t", " ")
    return s
