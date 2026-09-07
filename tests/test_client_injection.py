"""Tests for the optional client-injection seam in AsyncBrokerHandler."""

import asyncio
import logging

import pytest

from scietex.logging.message_broker_handler import AsyncBrokerHandler


def _make_record(message: str = "test message") -> logging.LogRecord:
    return logging.LogRecord(
        name="TestLogger",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=None,
    )


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError("condition was not met before timeout")
        await asyncio.sleep(0.01)


class RecordingClient:
    """Fake externally-managed broker client recording close() calls."""

    def __init__(self):
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class InjectedFakeBrokerHandler(AsyncBrokerHandler):
    """Concrete broker handler recording connect/send activity for tests."""

    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, str]] = []
        self.connect_attempts = 0
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        self.sent.append(record)


class FlakyInjectedBrokerHandler(AsyncBrokerHandler):
    """Broker whose send_message fails a fixed number of times before succeeding."""

    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, str]] = []
        self.connect_attempts = 0
        self.send_attempts = 0
        self.failures_before_success = 0
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        self.send_attempts += 1
        if self.send_attempts <= self.failures_before_success:
            raise RuntimeError("broker down")
        self.sent.append(record)


def test_injected_client_wiring_immediate():
    """Passing ``client=`` marks the handler as non-owning and wires the client."""
    client = RecordingClient()
    handler = InjectedFakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        client=client,
    )

    assert handler._owns_client is False
    assert handler.client is client
    assert handler._injected_client is client


@pytest.mark.asyncio
async def test_injected_client_never_closed_on_stop():
    """An injected client is delivered, and stop_logging never closes it."""
    client = RecordingClient()
    handler = InjectedFakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        client=client,
    )

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: len(handler.sent) == 1)
    await handler.stop_logging(timeout=0.5)

    assert handler.sent[0]["message"] == "hello"
    assert client.close_calls == 0
    assert handler._injected_client is client
    # The worker teardown clears the live reference but never the injected one.
    assert handler.client is None


@pytest.mark.asyncio
async def test_injected_client_reused_across_restart():
    """The same injected client is reused across start/stop cycles; no reconnect."""
    client = RecordingClient()
    handler = InjectedFakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        client=client,
    )

    await handler.start_logging()
    handler.emit(_make_record("first"))
    await _wait_for(lambda: len(handler.sent) == 1)
    await handler.stop_logging(timeout=0.5)

    await handler.start_logging()
    handler.emit(_make_record("second"))
    await _wait_for(lambda: len(handler.sent) == 2)
    await handler.stop_logging(timeout=0.5)

    assert [r["message"] for r in handler.sent] == ["first", "second"]
    assert handler._injected_client is client
    assert client.close_calls == 0
    assert handler.connect_attempts == 0


@pytest.mark.asyncio
async def test_injected_client_send_failure_does_not_close_or_reconnect():
    """A send failure leaves the injected client open and reused, never rebuilt."""
    client = RecordingClient()
    errors = []
    handler = FlakyInjectedBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        client=client,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.failures_before_success = 1  # first send fails, then succeeds

    await handler.start_logging()
    handler.emit(_make_record("one"))
    handler.emit(_make_record("two"))
    await _wait_for(lambda: len(handler.sent) == 1)

    assert handler.sent[0]["message"] == "two"
    assert len(errors) == 1
    assert client.close_calls == 0
    assert handler.connect_attempts == 0
    assert handler._injected_client is client

    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_default_no_client_manages_own_connection():
    """Without injection the handler owns its client: connects on start, clears on stop."""
    handler = InjectedFakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
    )

    assert handler._owns_client is True
    assert handler._injected_client is None
    assert handler.client is None

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: len(handler.sent) == 1)
    await handler.stop_logging(timeout=0.5)

    assert handler.connect_attempts == 1
    assert handler.client is None


# --- Mutual-exclusivity guard (client vs backend config) ---


def test_base_client_and_backend_config_raise():
    """Passing both client and backend_config to the base raises ValueError."""
    client = RecordingClient()
    with pytest.raises(ValueError):
        InjectedFakeBrokerHandler(
            queue_name="broker",
            stdout_enable=False,
            client=client,
            backend_config=object(),
        )


def test_base_client_only_no_error():
    """Passing only client to the base constructs without error."""
    client = RecordingClient()
    handler = InjectedFakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        client=client,
    )
    assert handler._owns_client is False
    assert handler.client is client


def test_valkey_client_and_config_raise():
    """AsyncValkeyHandler rejects both client and valkey_config."""
    valkey = pytest.importorskip("scietex.logging.valkey_handler")
    client = object()
    with pytest.raises(ValueError):
        valkey.AsyncValkeyHandler(
            stream_name="s",
            stdout_enable=False,
            client=client,
            valkey_config={"addresses": [("localhost", 6379)]},
        )


def test_valkey_client_only_no_error():
    """AsyncValkeyHandler accepts client alone (config unused, no spurious raise)."""
    valkey = pytest.importorskip("scietex.logging.valkey_handler")
    client = object()
    handler = valkey.AsyncValkeyHandler(
        stream_name="s",
        stdout_enable=False,
        client=client,
    )
    assert handler._owns_client is False
    assert handler.client is client
    assert handler.config.backend_config is None


def test_redis_client_and_config_raise():
    """AsyncRedisHandler rejects both client and redis_config."""
    redis_mod = pytest.importorskip("scietex.logging.redis_handler")
    client = object()
    with pytest.raises(ValueError):
        redis_mod.AsyncRedisHandler(
            stream_name="s",
            stdout_enable=False,
            client=client,
            redis_config={"host": "localhost"},
        )


def test_redis_client_only_no_error():
    """AsyncRedisHandler accepts client alone (config unused, no spurious raise)."""
    redis_mod = pytest.importorskip("scietex.logging.redis_handler")
    client = object()
    handler = redis_mod.AsyncRedisHandler(
        stream_name="s",
        stdout_enable=False,
        client=client,
    )
    assert handler._owns_client is False
    assert handler.client is client
    assert handler.config.backend_config is None
