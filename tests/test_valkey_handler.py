"""Tests for AsyncValkeyHandler."""

import asyncio
import logging
import socket

import pytest
from glide import (
    GlideClient,
    GlideClientConfiguration,
    MaxId,
    MinId,
    NodeAddress,
    ServerCredentials,
)

from scietex.logging import (
    AsyncValkeyHandler,
)  # Replace with actual module path


def _valkey_server_reachable() -> bool:
    """Return True when a TCP connection to the local Valkey node can be established."""
    try:
        with socket.create_connection(("localhost", 6379), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _valkey_server_reachable(),
    reason="No Valkey server reachable at localhost:6379; skipping end-to-end test.",
)
@pytest.mark.asyncio
async def test_valkey_handler_logs_to_stream():
    """Testing logging to stream."""
    # Configuration for the Valkey connection and stream
    stream_name = "test_log_stream"
    client_config: GlideClientConfiguration = GlideClientConfiguration(
        [NodeAddress(host="localhost", port=6379)]
    )

    valkey_client = await GlideClient.create(client_config)

    # Clear the test stream if it exists
    await valkey_client.delete([stream_name])
    service_name = "TestLogger"
    worker_id = 1
    # Create the Valkey log handler
    handler = AsyncValkeyHandler(
        service_name=service_name,
        worker_id=worker_id,
        stream_name=stream_name,
        valkey_config={"addresses": [("localhost", 6379)]},
    )
    await handler.start_logging()  # Start the handler logging workers

    # Setup logger and attach the handler
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Log a test message
    messages_number = 10
    test_messages = [f"Test Valkey log message {i}" for i in range(messages_number)]
    for message in test_messages:
        logger.info(message)

    # Allow some time for the worker to process the log message
    await asyncio.sleep(1)

    messages = await valkey_client.xrange(stream_name, MinId(), MaxId(), count=messages_number)
    assert messages is not None, "No messages found in the Valkey stream."
    assert len(messages) == messages_number, "No messages found in the Valkey stream."
    messages_data = iter(messages.values())

    # Fetch the latest entry from the Valkey stream
    for message in test_messages:
        # Decode the message data from bytes to strings
        message_data = next(messages_data)
        print(message_data)
        decoded_message_data = {
            key.decode("utf-8"): value.decode("utf-8") for key, value in message_data
        }
        print(decoded_message_data)
        # Check the contents of the log entry
        assert decoded_message_data["message"] == message, (
            "Valkey logger:Log message data mismatch."
        )
        assert decoded_message_data["level"] == "INF", "Valkey logger: Log level mismatch."
        assert decoded_message_data["name"] == f"{service_name}:{worker_id}", (
            "Valkey logger:Logger name mismatch."
        )

    # Clean up
    await handler.stop_logging()
    await valkey_client.delete([stream_name])  # Clear the test stream after the test
    await valkey_client.close()  # Close the Valkey client connection


def test_valkey_config_is_typed():
    """valkey_config dict is reflected in a typed ValkeyConfig on the handler."""
    handler = AsyncValkeyHandler(
        stream_name="s",
        valkey_config={"addresses": [("example.com", 7000)]},
    )
    assert handler.config.backend_config.addresses == [("example.com", 7000)]
    # client_config is a read-only asdict view of the typed config.
    assert handler.client_config["addresses"] == [("example.com", 7000)]


def test_valkey_config_defaults():
    """No valkey_config defaults to a localhost:6379 ValkeyConfig."""
    handler = AsyncValkeyHandler(stream_name="s")
    assert handler.config.backend_config.addresses == [("localhost", 6379)]
    assert handler.client_config["addresses"] == [("localhost", 6379)]


@pytest.mark.asyncio
async def test_valkey_connect_translates_config_to_glide(monkeypatch):
    """connect() reads backend_config and translates it into a GlideClientConfiguration."""
    captured = {}

    async def fake_create(config):
        captured["config"] = config
        return None

    monkeypatch.setattr(GlideClient, "create", fake_create)
    handler = AsyncValkeyHandler(
        stream_name="s",
        valkey_config={"addresses": [("localhost", 6379)], "request_timeout": 100},
    )
    assert handler.config.backend_config.request_timeout == 100
    await handler.connect()
    config = captured["config"]
    assert isinstance(config, GlideClientConfiguration)
    assert [(node.host, node.port) for node in config.addresses] == [("localhost", 6379)]
    assert config.request_timeout == 100


@pytest.mark.asyncio
async def test_valkey_connect_translates_credentials_to_glide(monkeypatch):
    """connect() builds a ServerCredentials from username/password and passes it to glide."""
    captured = {}

    async def fake_create(config):
        captured["config"] = config
        return None

    monkeypatch.setattr(GlideClient, "create", fake_create)
    handler = AsyncValkeyHandler(
        stream_name="s",
        valkey_config={
            "addresses": [("localhost", 6379)],
            "username": "svc",
            "password": "secret",
        },
    )
    await handler.connect()
    config = captured["config"]
    assert isinstance(config, GlideClientConfiguration)
    assert isinstance(config.credentials, ServerCredentials)
    assert config.credentials.username == "svc"
    assert config.credentials.password == "secret"


@pytest.mark.asyncio
async def test_valkey_connect_omits_credentials_when_unset(monkeypatch):
    """connect() leaves glide's credentials default when no username/password is given."""
    captured = {}

    async def fake_create(config):
        captured["config"] = config
        return None

    monkeypatch.setattr(GlideClient, "create", fake_create)
    handler = AsyncValkeyHandler(
        stream_name="s",
        valkey_config={"addresses": [("localhost", 6379)]},
    )
    await handler.connect()
    config = captured["config"]
    assert isinstance(config, GlideClientConfiguration)
    assert config.credentials is None


def test_valkey_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError):
        AsyncValkeyHandler(stream_name="s", stdout_enabel=True)


@pytest.mark.asyncio
async def test_valkey_send_message_raises_when_not_connected():
    """send_message() raises instead of silently acking when no client is connected (AR-034)."""
    handler = AsyncValkeyHandler(stream_name="s")
    record = {"level": "INF", "message": "m", "name": "n", "time": "t"}
    with pytest.raises(RuntimeError):
        await handler.send_message(record)
