"""Tests for the ``instance_id`` parameter and the deprecated ``worker_id`` alias."""

import warnings

import pytest

from scietex.logging import AsyncBaseHandler, AsyncFileHandler, ScietexFormatter
from scietex.logging.config import LoggingConfig, resolve_instance_id
from scietex.logging.mqtt_handler import AsyncMqttHandler
from scietex.logging.redis_handler import AsyncRedisHandler
from scietex.logging.valkey_handler import AsyncValkeyHandler


def test_instance_id_only_reflects_in_worker_name():
    """instance_id alone is used verbatim as the identity."""
    handler = AsyncBaseHandler(service_name="Svc", instance_id="web-1")
    assert handler.worker_name == "Svc:web-1"
    assert handler.config.instance_id == "web-1"
    assert handler.config.worker_id == 1  # worker_id untouched when not supplied
    assert handler.formatter.worker_name == "Svc:web-1"


def test_worker_id_only_is_deprecated_and_stringified():
    """worker_id alone is stringified into instance_id and reflected in worker_name."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        handler = AsyncBaseHandler(service_name="Svc", worker_id=42)
    assert handler.worker_name == "Svc:42"
    assert handler.config.instance_id == "42"
    assert handler.config.worker_id == 42
    assert handler.formatter.worker_name == "Svc:42"


def test_both_provided_raises_value_error():
    """Supplying both worker_id and instance_id is contradictory and raises."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        AsyncBaseHandler(service_name="Svc", worker_id=1, instance_id="web-1")


def test_neither_defaults_to_instance_id_1():
    """With neither supplied, instance_id defaults to the string "1"."""
    handler = AsyncBaseHandler(service_name="Svc")
    assert handler.worker_name == "Svc:1"
    assert handler.config.instance_id == "1"
    assert handler.config.worker_id == 1
    assert handler.formatter.worker_name == "Svc:1"


def test_worker_id_emits_deprecation_warning():
    """Using worker_id emits a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="deprecated"):
        AsyncBaseHandler(service_name="Svc", worker_id=1)


def test_instance_id_emits_no_warning():
    """Using instance_id alone emits no DeprecationWarning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        AsyncBaseHandler(service_name="Svc", instance_id="web-1")


def test_file_handler_forwards_instance_id():
    """AsyncFileHandler forwards instance_id through to the base machinery."""
    handler = AsyncFileHandler("/tmp/instance_id.log", service_name="Svc", instance_id="fs-2")
    assert handler.worker_name == "Svc:fs-2"
    assert handler.config.instance_id == "fs-2"


@pytest.mark.parametrize(
    "factory",
    [
        lambda iid: AsyncRedisHandler(stream_name="logs", instance_id=iid),
        lambda iid: AsyncValkeyHandler(stream_name="logs", instance_id=iid),
        lambda iid: AsyncMqttHandler(topic="logs", instance_id=iid),
    ],
    ids=["redis", "valkey", "mqtt"],
)
def test_broker_handlers_forward_instance_id(factory):
    """Broker handlers forward instance_id through the forwarding chain."""
    handler = factory("broker-3")
    assert handler.worker_name == "Service:broker-3"
    assert handler.config.instance_id == "broker-3"


def test_formatter_instance_id_only():
    formatter = ScietexFormatter(service_name="Svc", instance_id="web-1")
    assert formatter.worker_name == "Svc:web-1"


def test_formatter_worker_id_deprecated():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        formatter = ScietexFormatter(service_name="Svc", worker_id=7)
    assert formatter.worker_name == "Svc:7"


def test_formatter_defaults_to_instance_id_1():
    formatter = ScietexFormatter(service_name="Svc")
    assert formatter.worker_name == "Svc:1"


def test_formatter_both_raises_value_error():
    with pytest.raises(ValueError, match="mutually exclusive"):
        ScietexFormatter(service_name="Svc", worker_id=1, instance_id="web-1")


def test_formatter_worker_id_emits_deprecation_warning():
    with pytest.warns(DeprecationWarning, match="deprecated"):
        ScietexFormatter(service_name="Svc", worker_id=1)


def test_logging_config_defaults_to_instance_id_1():
    cfg = LoggingConfig()
    assert cfg.instance_id == "1"
    assert cfg.worker_id == 1  # deprecated field still present for backward compat


@pytest.mark.parametrize(
    ("worker_id", "instance_id", "expected"),
    [
        (None, None, "1"),
        (None, "web-1", "web-1"),
        (42, None, "42"),
    ],
)
def test_resolve_instance_id(worker_id, instance_id, expected):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert resolve_instance_id(worker_id, instance_id) == expected


def test_resolve_instance_id_rejects_both():
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_instance_id(1, "web-1")
