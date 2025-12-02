def get_float(v):
    """
    Convert a value to float, handling None and exceptions gracefully.

    Attempts to convert the input value to a float. If the value is None or
    cannot be converted to float, returns negative infinity as a default value.

    Args:
        v: The value to convert to float. Can be any type that float() accepts,
           or None.

    Returns:
        float: The converted float value if successful, otherwise float('-inf').

    Examples:
        >>> get_float("3.14")
        3.14
        >>> get_float(None)
        -inf
        >>> get_float("invalid")
        -inf
        >>> get_float(42)
        42.0
    """
    if v is None:
        return float('-inf')
    try:
        return float(v)
    except Exception:
        return float('-inf')