"""Configuration resolution and the producer-only Celery app."""

from __future__ import annotations

import pytest

from task_bridge import BridgeConfig, BridgeConfigurationError


def test_env_broker_and_backend_win_over_redis_derivation():
    config = BridgeConfig.from_env(
        {
            "CELERY_BROKER_URL": "amqp://user:pw@rabbit:5672/vhost",
            "CELERY_RESULT_BACKEND": "redis://cache:6379/4",
            "REDIS_HOST": "ignored",
        }
    )

    assert config.broker_url == "amqp://user:pw@rabbit:5672/vhost"
    assert config.result_backend == "redis://cache:6379/4"


def test_bridge_specific_variables_win_over_celery_variables():
    config = BridgeConfig.from_env(
        {
            "TASK_BRIDGE_BROKER_URL": "redis://bridge/3",
            "CELERY_BROKER_URL": "redis://api/3",
            "TASK_BRIDGE_RESULT_BACKEND": "redis://bridge/4",
            "CELERY_RESULT_BACKEND": "redis://api/4",
        }
    )

    assert config.broker_url == "redis://bridge/3"
    assert config.result_backend == "redis://bridge/4"


def test_redis_variables_derive_the_api_databases():
    config = BridgeConfig.from_env(
        {
            "REDIS_HOST": "redis",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "p@ss:word",
            "REDIS_DB_CELERY_BROKER": "7",
            "REDIS_DB_CELERY_BACKEND": "8",
        }
    )

    assert config.broker_url == "redis://:p%40ss%3Aword@redis:6380/7"
    assert config.result_backend == "redis://:p%40ss%3Aword@redis:6380/8"


def test_redis_url_is_reused_but_the_database_is_replaced():
    config = BridgeConfig.from_env(
        {
            "REDIS_URL": "rediss://:pw@cache.example:6380/1",
            "REDIS_DB_CELERY_BROKER": "3",
            "REDIS_DB_CELERY_BACKEND": "4",
        }
    )

    assert config.broker_url == "rediss://:pw@cache.example:6380/3"
    assert config.result_backend == "rediss://:pw@cache.example:6380/4"


def test_bridge_database_override():
    config = BridgeConfig.from_env(
        {"REDIS_HOST": "redis", "TASK_BRIDGE_BROKER_DB": "9", "TASK_BRIDGE_BACKEND_DB": "10"}
    )

    assert config.broker_url.endswith("/9")
    assert config.result_backend.endswith("/10")


def test_defaults_match_api_celery_app():
    config = BridgeConfig.from_env({})

    assert config.broker_url == "redis://127.0.0.1:6379/3"
    assert config.result_backend == "redis://127.0.0.1:6379/4"


def test_queue_routes_and_validation_flag_from_env():
    config = BridgeConfig.from_env(
        {
            "TASK_BRIDGE_DEFAULT_QUEUE": "memory_tasks",
            "TASK_BRIDGE_TASK_ROUTES": '{"app.tasks.x": "memory_heavy_tasks"}',
            "TASK_BRIDGE_VALIDATE_PAYLOAD": "false",
        }
    )

    assert config.default_queue == "memory_tasks"
    assert config.task_routes == {"app.tasks.x": "memory_heavy_tasks"}
    assert config.validate_payload is False


def test_async_and_executor_settings_from_env():
    config = BridgeConfig.from_env(
        {
            "TASK_BRIDGE_ASYNC_REDIS_RESULTS": "false",
            "TASK_BRIDGE_ASYNC_REDIS_MAX_CONNECTIONS": "10",
            "TASK_BRIDGE_EXECUTOR_WORKERS": "4",
        }
    )

    assert config.prefer_async_redis_results is False
    assert config.async_redis_max_connections == 10
    assert config.executor_workers == 4


def test_invalid_pool_and_executor_sizes_are_rejected():
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_EXECUTOR_WORKERS": "0"})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_ASYNC_REDIS_MAX_CONNECTIONS": "nope"})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig(broker_url="memory://", executor_workers=0)
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_BROKER_POOL_LIMIT": "0"})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig(broker_url="memory://", async_redis_max_connections=0)


def test_broker_pool_limit_is_configurable():
    default_app = BridgeConfig(broker_url="memory://").build_app()
    tuned_app = BridgeConfig(broker_url="memory://", broker_pool_limit=32).build_app()
    env_config = BridgeConfig.from_env({"TASK_BRIDGE_BROKER_POOL_LIMIT": "24"})
    try:
        assert default_app.conf.broker_pool_limit == 10  # Celery's default
        assert tuned_app.conf.broker_pool_limit == 32
        assert env_config.broker_pool_limit == 24
    finally:
        default_app.close()
        tuned_app.close()


def test_pubsub_toggle_from_env():
    assert BridgeConfig.from_env({"TASK_BRIDGE_REDIS_PUBSUB": "false"}).use_redis_pubsub is False
    assert BridgeConfig.from_env({}).use_redis_pubsub is True


def test_poll_jitter_from_env_and_validation():
    assert BridgeConfig.from_env({"TASK_BRIDGE_POLL_JITTER": "0.25"}).poll_jitter == 0.25
    assert BridgeConfig.from_env({}).poll_jitter == 0.1
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig(broker_url="memory://", poll_jitter=1.5)
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_POLL_JITTER": "nope"})


def test_invalid_env_values_raise_configuration_errors():
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"REDIS_DB_CELERY_BROKER": "not-a-number"})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_TASK_ROUTES": "not-json"})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_TASK_ROUTES": '{"x": 1}'})
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig.from_env({"TASK_BRIDGE_VALIDATE_PAYLOAD": "maybe"})


def test_empty_broker_is_rejected():
    with pytest.raises(BridgeConfigurationError):
        BridgeConfig(broker_url="")


def test_build_app_keeps_the_json_contract_and_queue_routing():
    config = BridgeConfig(
        broker_url="redis://localhost:6379/3",
        result_backend="redis://localhost:6379/4",
        default_queue="memory_tasks",
        task_routes={"app.tasks.x": "memory_heavy_tasks"},
    )
    app = config.build_app()

    try:
        assert app.conf.broker_url == "redis://localhost:6379/3"
        assert app.conf.result_backend == "redis://localhost:6379/4"
        assert app.conf.task_default_queue == "memory_tasks"
        assert app.conf.task_routes == {"app.tasks.x": {"queue": "memory_heavy_tasks"}}
        assert app.conf.task_serializer == "json"
        assert app.conf.result_serializer == "json"
        assert app.conf.accept_content == ["json"]
        # The bridge must not replace the process-wide current app.
        assert app.set_as_current is False
    finally:
        app.close()


def test_explicit_settings_win_over_app_options():
    config = BridgeConfig(
        broker_url="memory://",
        app_options={"task_serializer": "pickle", "worker_prefetch_multiplier": 4},
    )
    app = config.build_app()

    try:
        assert app.conf.task_serializer == "json"
        assert app.conf.worker_prefetch_multiplier == 4
    finally:
        app.close()


def test_redis_socket_timeouts_are_applied_by_default():
    config = BridgeConfig(
        broker_url="redis://localhost:6379/3",
        result_backend="redis://localhost:6379/4",
    )
    app = config.build_app()

    try:
        assert app.conf.redis_socket_timeout == 5.0
        assert app.conf.redis_socket_connect_timeout == 5.0
        assert app.conf.broker_transport_options["socket_timeout"] == 5.0
        assert app.conf.broker_transport_options["socket_connect_timeout"] == 5.0
    finally:
        app.close()


def test_app_options_win_over_redis_timeout_defaults():
    config = BridgeConfig(
        broker_url="redis://localhost:6379/3",
        result_backend="redis://localhost:6379/4",
        app_options={
            "redis_socket_timeout": 1.0,
            "broker_transport_options": {"socket_timeout": 1.5},
        },
    )
    app = config.build_app()

    try:
        assert app.conf.redis_socket_timeout == 1.0
        assert app.conf.redis_socket_connect_timeout == 5.0
        assert app.conf.broker_transport_options["socket_timeout"] == 1.5
        assert app.conf.broker_transport_options["socket_connect_timeout"] == 5.0
    finally:
        app.close()


def test_redis_timeouts_can_be_left_to_celery_defaults():
    config = BridgeConfig(
        broker_url="redis://localhost:6379/3",
        result_backend="redis://localhost:6379/4",
        redis_socket_timeout=None,
        redis_socket_connect_timeout=None,
    )
    app = config.build_app()

    try:
        # Celery's own defaults: 120s read timeout, no connect timeout.
        assert app.conf.redis_socket_timeout == 120.0
        assert app.conf.redis_socket_connect_timeout is None
        assert not (app.conf.broker_transport_options or {}).get("socket_timeout")
    finally:
        app.close()


def test_non_redis_transports_are_untouched():
    config = BridgeConfig(broker_url="amqp://guest:guest@rabbit:5672//", result_backend="rpc://")
    app = config.build_app()

    try:
        # Our 5s defaults must not leak into non-Redis transports.
        assert app.conf.redis_socket_connect_timeout is None
        assert not (app.conf.broker_transport_options or {})
    finally:
        app.close()
