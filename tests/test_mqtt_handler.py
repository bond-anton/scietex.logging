"""Tests for AsyncMqttHandler."""

import asyncio
import json
import logging
import socket
from dataclasses import asdict

import pytest

from scietex.logging.config import MqttConfig
from scietex.logging.handler.mqtt import AsyncMqttHandler


def _mqtt_server_reachable() -> bool:
    """Return True when a TCP connection to the local MQTT broker can be established."""
    try:
        with socket.create_connection(("localhost", 1883), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _mqtt_server_reachable(),
    reason="No MQTT broker reachable at localhost:1883; skipping end-to-end test.",
)
@pytest.mark.asyncio
async def test_mqtt_handler_publishes_to_topic():
    """Testing publishing to an MQTT topic."""
    import aiomqtt

    topic = "test/log/topic"
    service_name = "TestLogger"

    # Subscribe with a separate client to capture the published message.
    received = []

    async def _capture():
        async with aiomqtt.Client("localhost", 1883) as sub_client:
            await sub_client.subscribe(topic)
            async for message in sub_client.messages:
                received.append(message.payload.decode("utf-8"))
                break

    capture_task = asyncio.create_task(_capture())
    await asyncio.sleep(0.5)  # let the subscriber connect and subscribe

    handler = AsyncMqttHandler(
        topic=topic,
        mqtt_config={"host": "localhost", "port": 1883},
    )
    await handler.start_logging()

    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    test_message = "Test MQTT log message"
    logger.info(test_message)

    await asyncio.wait_for(capture_task, timeout=5.0)
    await handler.stop_logging()

    assert len(received) == 1, "No message received on the MQTT topic."
    payload = json.loads(received[0])
    assert payload["message"] == test_message, "Log message content mismatch."
    assert payload["level"] == "INF", "Log level mismatch."
    assert payload["name"] == service_name, "Logger name mismatch."


def test_mqtt_config_is_typed_and_validated():
    """mqtt_config dict is converted into a typed MqttConfig on the handler."""
    handler = AsyncMqttHandler(
        topic="s",
        mqtt_config={"host": "example.com", "port": 8883},
    )
    assert handler.config.backend_config.host == "example.com"
    assert handler.config.backend_config.port == 8883
    # client_config is a read-only asdict view of the typed config.
    assert handler.client_config == asdict(MqttConfig(host="example.com", port=8883))


def test_mqtt_config_defaults():
    """No mqtt_config defaults to a localhost:1883 MqttConfig."""
    handler = AsyncMqttHandler(topic="s")
    assert handler.config.backend_config.host == "localhost"
    assert handler.config.backend_config.port == 1883
    assert handler.client_config["host"] == "localhost"
    assert handler.client_config["port"] == 1883


def test_mqtt_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError):
        AsyncMqttHandler(topic="s", unknown_kwarg=True)


@pytest.mark.asyncio
async def test_mqtt_connect_translates_host_to_hostname(monkeypatch):
    """connect() reads backend_config and translates host -> aiomqtt hostname."""
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("aiomqtt.Client", fake_client)
    handler = AsyncMqttHandler(
        topic="s",
        mqtt_config={"host": "example.com", "port": 8883, "keepalive": 60},
    )
    await handler.connect()
    assert captured["hostname"] == "example.com"
    assert captured["port"] == 8883
    assert captured["keepalive"] == 60
    assert "host" not in captured  # host is translated, never passed through


@pytest.mark.asyncio
async def test_mqtt_connect_omits_none_fields(monkeypatch):
    """connect() drops None-valued fields so aiomqtt applies its own defaults."""
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("aiomqtt.Client", fake_client)
    handler = AsyncMqttHandler(topic="s", mqtt_config={"host": "localhost"})
    await handler.connect()
    assert captured["hostname"] == "localhost"
    assert "username" not in captured
    assert "keepalive" not in captured


@pytest.mark.asyncio
async def test_mqtt_disconnect_exits_context(monkeypatch):
    """disconnect() calls __aexit__ and clears the client."""
    exited = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            exited.append((exc_type, exc, tb))

    monkeypatch.setattr("aiomqtt.Client", lambda **kwargs: FakeClient())
    handler = AsyncMqttHandler(topic="s")
    await handler.connect()
    assert handler.client is not None
    await handler.disconnect()
    assert handler.client is None
    assert exited == [(None, None, None)]


@pytest.mark.asyncio
async def test_mqtt_send_message_publishes_json(monkeypatch):
    """send_message() publishes a JSON payload to the configured topic."""
    published = {}

    class FakeClient:
        async def publish(self, topic, payload, *, qos, retain):
            published["topic"] = topic
            published["payload"] = payload
            published["qos"] = qos
            published["retain"] = retain

    handler = AsyncMqttHandler(topic="logs", qos=1, retain=True)
    handler.client = FakeClient()
    record = {"level": "INF", "message": "m", "name": "n", "time": "t"}
    await handler.send_message(record)
    assert published["topic"] == "logs"
    assert json.loads(published["payload"]) == record
    assert published["qos"] == 1
    assert published["retain"] is True


@pytest.mark.asyncio
async def test_mqtt_send_message_raises_when_not_connected():
    """send_message() raises instead of silently acking when no client is connected (AR-034)."""
    handler = AsyncMqttHandler(topic="s")
    record = {"level": "INF", "message": "m", "name": "n", "time": "t"}
    with pytest.raises(RuntimeError):
        await handler.send_message(record)
