"""Tests for AsyncRedisHandler."""

import asyncio
import logging
import socket
from dataclasses import asdict

import pytest
from redis.asyncio import Redis

from scietex.logging.config import RedisConfig
from scietex.logging.handler.redis import AsyncRedisHandler


def _redis_server_reachable() -> bool:
    """Return True when a TCP connection to the local Redis node can be established."""
    try:
        with socket.create_connection(("localhost", 6379), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _redis_server_reachable(),
    reason="No Redis server reachable at localhost:6379; skipping end-to-end test.",
)
@pytest.mark.asyncio
async def test_redis_handler_logs_to_stream():
    """Testing logging to stream."""
    # Configuration for the Redis connection and stream
    stream_name = "test_log_stream"
    redis_config = {"host": "localhost", "port": 6379, "db": 0}

    # Initialize Redis client to interact with the stream directly
    redis_client = Redis(
        host=redis_config["host"], port=redis_config["port"], db=redis_config["db"]
    )

    # Clear the test stream if it exists
    await redis_client.delete(stream_name)
    service_name = "TestLogger"
    # Create the Redis log handler
    handler = AsyncRedisHandler(
        stream_name=stream_name,
        redis_config=redis_config,
    )
    await handler.start_logging()  # Start the handler logging workers

    # Setup logger and attach the handler
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Log a test message
    test_message = "Test log message"
    logger.info(test_message)

    # Allow some time for the worker to process the log message
    await asyncio.sleep(1)

    # Fetch the latest entry from the Redis stream
    messages = await redis_client.xrange(stream_name, count=1)
    print(messages)
    assert len(messages) == 1, "No messages found in the Redis stream."

    # Decode the message data from bytes to strings
    _, message_data = messages[0]
    decoded_message_data = {
        key.decode("utf-8"): value.decode("utf-8") for key, value in message_data.items()
    }
    print(decoded_message_data)
    # Check the contents of the log entry
    assert decoded_message_data["message"] == test_message, "Log message content mismatch."
    assert decoded_message_data["level"] == "INF", "Log level mismatch."
    assert decoded_message_data["name"] == service_name, "Logger name mismatch."

    # Clean up
    await handler.stop_logging()
    await redis_client.delete(stream_name)  # Clear the test stream after the test
    await redis_client.aclose()  # Close the Redis client connection


def test_redis_config_is_typed_and_validated():
    """redis_config dict is converted into a typed RedisConfig on the handler."""
    handler = AsyncRedisHandler(
        stream_name="s",
        redis_config={"host": "example.com", "port": 7000, "db": 2},
    )
    assert handler.config.backend_config.host == "example.com"
    assert handler.config.backend_config.port == 7000
    assert handler.config.backend_config.db == 2
    # client_config is a read-only asdict view of the typed config.
    assert handler.client_config == asdict(RedisConfig(host="example.com", port=7000, db=2))


def test_redis_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError):
        AsyncRedisHandler(stream_name="s", unknown_kwarg=True)


def test_redis_config_accepts_valid_extra_options():
    """A redis_config dict with legitimate client options no longer raises TypeError."""
    handler = AsyncRedisHandler(
        stream_name="s",
        redis_config={
            "host": "example.com",
            "port": 7000,
            "db": 2,
            "password": "secret",
            "ssl": True,
        },
    )
    assert handler.config.backend_config.host == "example.com"
    assert handler.config.backend_config.password == "secret"
    assert handler.config.backend_config.ssl is True
    # client_config is a read-only asdict view of the typed config.
    assert handler.client_config == asdict(
        RedisConfig(host="example.com", port=7000, db=2, password="secret", ssl=True)
    )


@pytest.mark.asyncio
async def test_redis_connect_closes_client_when_ping_fails(monkeypatch):
    """connect() closes the locally-created client when ping() raises (AR-033)."""
    closed = []

    class FakeClient:
        async def ping(self):
            raise ConnectionError("ping failed")

        async def aclose(self):
            closed.append(True)

    fake_client = FakeClient()

    async def fake_redis(**kwargs):
        return fake_client

    monkeypatch.setattr("redis.asyncio.Redis", fake_redis)

    handler = AsyncRedisHandler(stream_name="s")
    with pytest.raises(ConnectionError):
        await handler.connect()
    assert handler.client is None
    assert closed == [True]


@pytest.mark.asyncio
async def test_redis_connect_respects_decode_responses_true(monkeypatch):
    """connect() honors a user-supplied decode_responses=True (AR-101)."""
    captured = {}

    class FakeClient:
        async def ping(self):
            return True

    async def fake_redis(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("redis.asyncio.Redis", fake_redis)
    handler = AsyncRedisHandler(stream_name="s", redis_config={"decode_responses": True})
    await handler.connect()
    assert captured["decode_responses"] is True


@pytest.mark.asyncio
async def test_redis_connect_decode_responses_defaults_false(monkeypatch):
    """connect() no longer forces decode_responses=True; the default is False (AR-101)."""
    captured = {}

    class FakeClient:
        async def ping(self):
            return True

    async def fake_redis(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr("redis.asyncio.Redis", fake_redis)
    handler = AsyncRedisHandler(stream_name="s")
    await handler.connect()
    assert captured["decode_responses"] is False


@pytest.mark.asyncio
async def test_redis_send_message_raises_when_not_connected():
    """send_message() raises instead of silently acking when no client is connected (AR-034)."""
    handler = AsyncRedisHandler(stream_name="s")
    record = {"level": "INF", "message": "m", "name": "n", "time": "t"}
    with pytest.raises(RuntimeError):
        await handler.send_message(record)
