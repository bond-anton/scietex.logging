"""Tests for the typed configuration objects (AR-008)."""

import logging

import pytest

from scietex.logging.config import (
    LoggingConfig,
    MqttConfig,
    RedisConfig,
    ValkeyConfig,
    iso_timestamp,
    level_abbreviation,
    optional_dependency_error,
    validate_queue_maxsize,
)


def test_logging_config_defaults():
    cfg = LoggingConfig()
    assert cfg.error_handler is None
    assert cfg.queue_maxsize == 10000


def test_logging_config_has_only_machinery_fields():
    """backend_config is broker-owned; LoggingConfig keeps only the machinery options."""
    assert set(LoggingConfig.__dataclass_fields__) == {"error_handler", "queue_maxsize"}


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


def test_valkey_config_defaults():
    cfg = ValkeyConfig()
    assert cfg.addresses == [("localhost", 6379)]


def test_valkey_config_accepts_scalar_plain_options():
    """ValkeyConfig mirrors GlideClientConfiguration's scalar plain options."""
    cfg = ValkeyConfig(
        addresses=[("example.com", 7000)],
        use_tls=True,
        request_timeout=5000,
        database_id=3,
        client_name="my-client",
        inflight_requests_limit=100,
        client_az="az-1",
        lazy_connect=True,
        read_only=True,
    )
    assert cfg.addresses == [("example.com", 7000)]
    assert cfg.use_tls is True
    assert cfg.request_timeout == 5000
    assert cfg.database_id == 3
    assert cfg.client_name == "my-client"
    assert cfg.inflight_requests_limit == 100
    assert cfg.client_az == "az-1"
    assert cfg.lazy_connect is True
    assert cfg.read_only is True


def test_valkey_config_scalar_options_default_to_none():
    """ValkeyConfig scalar options default to None so glide applies its own defaults."""
    cfg = ValkeyConfig()
    assert cfg.use_tls is None
    assert cfg.request_timeout is None
    assert cfg.database_id is None
    assert cfg.client_name is None
    assert cfg.inflight_requests_limit is None
    assert cfg.client_az is None
    assert cfg.lazy_connect is None
    assert cfg.read_only is None


def test_mqtt_config_defaults():
    cfg = MqttConfig()
    assert cfg.host == "localhost"
    assert cfg.port == 1883
    assert cfg.username is None
    assert cfg.password is None
    assert cfg.identifier is None
    assert cfg.keepalive is None
    assert cfg.clean_session is None
    assert cfg.transport is None
    assert cfg.timeout is None
    assert cfg.tls_insecure is None


def test_mqtt_config_accepts_scalar_plain_options():
    """MqttConfig mirrors aiomqtt.Client's scalar plain options."""
    cfg = MqttConfig(
        host="example.com",
        port=8883,
        username="svc",
        password="secret",
        identifier="my-client",
        keepalive=60,
        clean_session=False,
        transport="tcp",
        timeout=10.0,
        tls_insecure=True,
    )
    assert cfg.host == "example.com"
    assert cfg.port == 8883
    assert cfg.username == "svc"
    assert cfg.password == "secret"
    assert cfg.identifier == "my-client"
    assert cfg.keepalive == 60
    assert cfg.clean_session is False
    assert cfg.transport == "tcp"
    assert cfg.timeout == 10.0
    assert cfg.tls_insecure is True


def test_validate_queue_maxsize_accepts_positive_int():
    assert validate_queue_maxsize(5000) == 5000


@pytest.mark.parametrize("bad", [0, -1, 1.5, "10000", True, None])
def test_validate_queue_maxsize_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        validate_queue_maxsize(bad)


def test_level_abbreviation_lives_in_config():
    """level_abbreviation is importable from the neutral config leaf (AR-026)."""
    assert level_abbreviation(logging.DEBUG) == "DBG"
    assert level_abbreviation(logging.INFO) == "INF"
    assert level_abbreviation(logging.WARNING) == "WRN"
    assert level_abbreviation(logging.ERROR) == "ERR"
    assert level_abbreviation(logging.CRITICAL) == "CRT"
    assert level_abbreviation(999) == "999"


def test_iso_timestamp_returns_iso8601_utc():
    """iso_timestamp is importable from the neutral config leaf (AR-007)."""
    assert iso_timestamp(0) == "1970-01-01T00:00:00+00:00"
    assert iso_timestamp(1234567890.5).endswith("+00:00")


@pytest.mark.parametrize(
    ("module_name", "extra"),
    [("redis", "redis"), ("valkey-glide", "valkey")],
)
def test_optional_dependency_error_message(module_name, extra):
    msg = optional_dependency_error(module_name, extra)
    assert module_name in msg
    assert f"pip install scietex.logging[{extra}]" in msg
