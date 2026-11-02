"""Environment-variable parsing shared by the service and by backend packages.

Standard library only, so a backend package can use it without pulling in the
scheduler. A dataclass field ``foo_bar`` maps to the variable ``<PREFIX>FOO_BAR``;
parsing follows the field type (``int``, ``float``, ``str``, ``bool`` and ``X | None``).
"""

from __future__ import annotations

import math
import types
import typing
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


class ConfigError(ValueError):
    """A configuration value is missing, malformed or out of range."""


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    """Return ``(inner_type, is_optional)`` for ``X | None`` annotations."""

    if typing.get_origin(tp) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return tp, False


def parse_value(name: str, raw: str, tp: Any) -> Any:
    inner, optional = _unwrap_optional(tp)
    text = raw.strip()
    if optional and text == "":
        return None
    try:
        if inner is bool:
            lowered = text.lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            raise ValueError("expected one of true/false/1/0/yes/no")
        if inner is int:
            return int(text)
        if inner is float:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError("not a finite number")
            return value
        if inner is str:
            if not text:
                raise ValueError("must not be empty")
            return text
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r}: {exc}") from exc
    raise ConfigError(f"{name}: unsupported field type {tp!r}")  # pragma: no cover


def variable_name(prefix: str, field_name: str) -> str:
    return f"{prefix}{field_name.upper()}"


def collect(cls: type, env: Mapping[str, str], prefix: str) -> dict[str, Any]:
    """Parse every field of dataclass ``cls`` that is present in ``env``."""

    hints = typing.get_type_hints(cls)
    values: dict[str, Any] = {}
    for f in fields(cls):
        name = variable_name(prefix, f.name)
        raw = env.get(name)
        if raw is None:
            continue
        values[f.name] = parse_value(name, raw, hints[f.name])
    return values


def build(cls: type, env: Mapping[str, str], prefix: str) -> Any:
    """Instantiate dataclass ``cls`` from ``env``; unset fields keep their defaults."""

    try:
        return cls(**collect(cls, env, prefix))
    except ConfigError:
        raise
    except TypeError as exc:  # a required field (no default) is missing
        raise ConfigError(f"invalid {cls.__name__}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"invalid {cls.__name__}: {exc}") from exc


def known_variables(classes: tuple[type, ...], prefix: str) -> list[str]:
    return sorted(variable_name(prefix, f.name) for cls in classes for f in fields(cls))
