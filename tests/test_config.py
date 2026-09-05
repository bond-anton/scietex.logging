"""Tests for the typed configuration objects (AR-008)."""

import typing

import pytest

from scietex.logging.config import (
    LoggingConfig,
    RedisConfig,
    ValkeyConfig,
    optional_dependency_error,
    validate_queue_maxsize,
)


def test_logging_config_defaults():
    cfg = LoggingConfig()
    assert cfg.service_name == "Service"
    assert cfg.worker_id == 1
    assert cfg.error_handler is None
    assert cfg.queue_maxsize == 10000
    assert cfg.stdout_enable is True
    assert cfg.backend_config is None


def test_logging_config_is_frozen():
    cfg = LoggingConfig()
    with pytest.raises(Exception):
        cfg.queue_maxsize = 5  # frozen dataclass rejects attribute assignment


def test_redis_config_defaults():
    cfg = RedisConfig()
    assert cfg.host == "localhost"
    assert cfg.port == 6379
    assert cfg.db == 0


def test_redis_config_accepts_full_client_option_surface():
    """RedisConfig mirrors the redis client options, so valid options are never rejected."""
    cfg = RedisConfig(
        host="example.com",
        port=7000,
        db=2,
        password="secret",
        username="svc",
        ssl=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        socket_keepalive=True,
        health_check_interval=30,
        decode_responses=True,
        client_name="my-client",
    )
    assert cfg.host == "example.com"
    assert cfg.port == 7000
    assert cfg.db == 2
    assert cfg.password == "secret"
    assert cfg.username == "svc"
    assert cfg.ssl is True
    assert cfg.socket_timeout == 5.0
    assert cfg.health_check_interval == 30
    assert cfg.decode_responses is True
    assert cfg.client_name == "my-client"


def test_backend_config_is_a_union_not_any():
    """LoggingConfig.backend_config is typed as a union of the backend configs, not Any."""
    hints = typing.get_type_hints(LoggingConfig)
    assert hints["backend_config"] == RedisConfig | ValkeyConfig | None


def test_valkey_config_defaults():
    cfg = ValkeyConfig()
    assert cfg.addresses == [("localhost", 6379)]


def test_validate_queue_maxsize_accepts_positive_int():
    assert validate_queue_maxsize(5000) == 5000


@pytest.mark.parametrize("bad", [0, -1, 1.5, "10000", True, None])
def test_validate_queue_maxsize_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        validate_queue_maxsize(bad)


@pytest.mark.parametrize(
    ("module_name", "extra"),
    [("redis", "redis"), ("valkey-glide", "valkey")],
)
def test_optional_dependency_error_message(module_name, extra):
    msg = optional_dependency_error(module_name, extra)
    assert module_name in msg
    assert f"pip install scietex.logging[{extra}]" in msg
