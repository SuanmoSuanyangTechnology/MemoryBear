from __future__ import annotations

from dataclasses import dataclass

import pytest

from src import envconf
from src.envconf import ConfigError


@dataclass(frozen=True)
class Sample:
    name: str
    count: int = 3
    ratio: float = 0.5
    flag: bool = False
    limit: int | None = None


P = "T_"


def test_unset_fields_keep_defaults():
    cfg = envconf.build(Sample, {"T_NAME": "x"}, P)
    assert cfg == Sample(name="x")


def test_each_supported_type_is_parsed():
    env = {"T_NAME": " abc ", "T_COUNT": "7", "T_RATIO": "1.25", "T_FLAG": "Yes", "T_LIMIT": "9"}
    cfg = envconf.build(Sample, env, P)
    assert cfg == Sample(name="abc", count=7, ratio=1.25, flag=True, limit=9)


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("no", False), ("0", False)])
def test_bool_spellings(raw, expected):
    assert envconf.build(Sample, {"T_NAME": "x", "T_FLAG": raw}, P).flag is expected


def test_blank_optional_means_none():
    assert envconf.build(Sample, {"T_NAME": "x", "T_LIMIT": "  "}, P).limit is None


@pytest.mark.parametrize(
    "var,raw",
    [
        ("T_COUNT", "many"),
        ("T_RATIO", "nan"),
        ("T_RATIO", "inf"),
        ("T_FLAG", "maybe"),
        ("T_NAME", "   "),
        ("T_LIMIT", "1.5"),
    ],
)
def test_bad_values_name_the_variable(var, raw):
    env = {"T_NAME": "x", var: raw}
    with pytest.raises(ConfigError, match=var):
        envconf.build(Sample, env, P)


def test_missing_required_field_is_a_config_error():
    with pytest.raises(ConfigError):
        envconf.build(Sample, {}, P)


def test_validation_error_from_the_dataclass_becomes_config_error():
    @dataclass(frozen=True)
    class Checked:
        n: int = 1

        def __post_init__(self) -> None:
            if self.n < 0:
                raise ValueError("n must be >= 0")

    with pytest.raises(ConfigError, match="n must be >= 0"):
        envconf.build(Checked, {"T_N": "-1"}, P)


def test_variable_names_and_known_variables():
    assert envconf.variable_name("SANDBOX_", "p_min_s") == "SANDBOX_P_MIN_S"
    assert envconf.known_variables((Sample,), P) == [
        "T_COUNT",
        "T_FLAG",
        "T_LIMIT",
        "T_NAME",
        "T_RATIO",
    ]
