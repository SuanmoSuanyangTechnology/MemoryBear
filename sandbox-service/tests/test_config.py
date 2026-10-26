from __future__ import annotations

import pytest

from src.config import ConfigError, ServiceConfig, known_variables, load_config
from src.services.scheduler_config import SchedulerConfig
from src.services.verdict import OutputBudget


def test_empty_env_keeps_defaults():
    cfg = load_config({})
    assert isinstance(cfg, ServiceConfig)
    assert cfg.scheduler == SchedulerConfig()
    assert cfg.budget == OutputBudget()


def test_defaults_match_decided_values():
    cfg = load_config({})
    assert cfg.scheduler.p_min_s == 2 * 3600
    assert cfg.scheduler.exec_timeout_default_s == 120.0
    assert cfg.scheduler.exec_timeout_max_s == 300.0
    assert cfg.budget.stdout_max_bytes == 1024 * 1024


def test_values_are_parsed_by_field_type():
    cfg = load_config(
        {
            "SANDBOX_CAPACITY": "200",
            "SANDBOX_P_MIN_S": "3600",
            "SANDBOX_HIGH_WATER": "0.8",
            "SANDBOX_LOW_WATER": "0.5",
            "SANDBOX_DEFAULT_TEMPLATE": "py312",
            "SANDBOX_STDOUT_MAX_BYTES": "2048",
            "SANDBOX_HEAD_RATIO": "0.25",
        }
    )
    assert cfg.scheduler.capacity == 200
    assert cfg.scheduler.p_min_s == 3600.0
    assert cfg.scheduler.high_water == 0.8
    assert cfg.scheduler.low_water == 0.5
    assert cfg.scheduler.default_template == "py312"
    assert cfg.budget.stdout_max_bytes == 2048
    assert cfg.budget.head_ratio == 0.25


def test_optional_fields_accept_value_and_empty():
    cfg = load_config({"SANDBOX_TENANT_CAPACITY": "10", "SANDBOX_TENANT_SOFT_QUOTA": ""})
    assert cfg.scheduler.tenant_capacity == 10
    assert cfg.scheduler.tenant_soft_quota is None


def test_whitespace_is_stripped():
    assert load_config({"SANDBOX_CAPACITY": " 7 "}).scheduler.capacity == 7


def test_unrelated_variables_are_ignored():
    cfg = load_config({"PATH": "/bin", "SANDBOX_UNKNOWN": "1"})
    assert cfg.scheduler == SchedulerConfig()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SANDBOX_CAPACITY", "abc"),
        ("SANDBOX_CAPACITY", "1.5"),
        ("SANDBOX_P_MIN_S", "soon"),
        ("SANDBOX_P_MIN_S", "nan"),
        ("SANDBOX_P_MIN_S", "inf"),
        ("SANDBOX_DEFAULT_TEMPLATE", "  "),
        ("SANDBOX_TENANT_CAPACITY", "x"),
    ],
)
def test_malformed_values_name_the_variable(name, value):
    with pytest.raises(ConfigError, match=name):
        load_config({name: value})


@pytest.mark.parametrize(
    "env",
    [
        {"SANDBOX_CAPACITY": "0"},
        {"SANDBOX_HIGH_WATER": "0.5", "SANDBOX_LOW_WATER": "0.6"},
        {"SANDBOX_EXEC_TIMEOUT_DEFAULT_S": "400"},
        {"SANDBOX_P_MIN_S": "-1"},
        {"SANDBOX_STDOUT_MAX_BYTES": "0"},
        {"SANDBOX_HEAD_RATIO": "1.5"},
    ],
)
def test_out_of_range_values_raise_config_error(env):
    with pytest.raises(ConfigError):
        load_config(env)


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("SANDBOX_CAPACITY", "33")
    assert load_config().scheduler.capacity == 33


def test_known_variables_cover_every_field():
    names = known_variables()
    assert names == sorted(names)
    assert "SANDBOX_CAPACITY" in names
    assert "SANDBOX_P_MIN_S" in names
    assert "SANDBOX_STDOUT_MAX_BYTES" in names
    assert "SANDBOX_EXEC_TIMEOUT_MAX_S" in names
