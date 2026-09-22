"""Tests for AsyncBrokerHandler abstract base class and its worker."""

import asyncio
import random
from dataclasses import asdict

import pytest
from conftest import CountingQueue, FakeBrokerHandler, _make_record, _wait_for

from scietex.logging.async_logging_handler import DrainStatus
from scietex.logging.config import MqttConfig, RedisConfig, ValkeyConfig
from scietex.logging.handler.broker import AsyncBrokerHandler
from scietex.logging.handler.mqtt import AsyncMqttHandler
from scietex.logging.handler.redis import AsyncRedisHandler
from scietex.logging.handler.valkey import AsyncValkeyHandler


class StuckConnectBrokerHandler(AsyncBrokerHandler):
    """Broker whose connect() blocks far longer than the stop timeout."""

    def __init__(self, *args, **kwargs):
        self.connect_started = False
        self.connect_cancelled = False
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_started = True
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.connect_cancelled = True
            raise
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        pass


class ScriptedConnectBrokerHandler(AsyncBrokerHandler):
    """Broker whose connect() outcome follows a script, failing once exhausted."""

    def __init__(self, *args, outcomes, send_error=None, **kwargs):
        self.outcomes = list(outcomes)
        self.connect_attempts = 0
        self.send_error = send_error
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        if self.outcomes and self.outcomes.pop(0) == "ok":
            self.client = object()
            return
        raise ConnectionError("connect failed")

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        if self.send_error is not None:
            raise self.send_error


@pytest.mark.asyncio
async def test_stop_logging_cancels_stuck_connect_worker():
    """stop_logging returns instead of deadlocking when connect() outlives the timeout."""
    handler = StuckConnectBrokerHandler(
        queue_name="broker",
    )

    await handler.start_logging()
    await _wait_for(lambda: handler.connect_started)

    # The worker is blocked inside connect() for ~10s, well past the 0.1s stop
    # timeout. stop_logging must cancel the straggler and return promptly rather
    # than hanging on an unreachable broker.
    await asyncio.wait_for(handler.stop_logging(timeout=0.1), timeout=5)

    assert handler.connect_cancelled
    assert handler.log_workers_tasks == []
    assert not handler.logging_running_event.is_set()


@pytest.mark.asyncio
async def test_connect_failure_surfaced_and_retried():
    """Connect failures are reported and the record is delivered after a retry."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.connect_failures = 1

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(handler.sent))
    assert handler.sent[0]["message"] == "hello"
    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert handler.connect_attempts == 2  # failed once, then retried and succeeded

    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_connect_retry_backs_off_exponentially(monkeypatch):
    """Consecutive connect() failures sleep a doubling, jitter-free, capped delay."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        error_handler=lambda record, exc: None,
    )
    handler.connect_failures = 10**9  # keep failing for the duration of the test

    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        delays.append(delay)
        await real_sleep(0.001)  # yield and rate-limit; the delay value is recorded

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)
    # Neutralize jitter so the recorded sequence is exactly base/doubling/cap.
    monkeypatch.setattr(random, "uniform", lambda a, b: (a + b) / 2)

    await handler.start_logging()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while len(delays) < 8 and loop.time() < deadline:
        await real_sleep(0.01)

    await handler.stop_logging(timeout=0.5)

    assert len(delays) >= 8
    assert delays[:8] == pytest.approx([0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0])


@pytest.mark.asyncio
async def test_connect_backoff_resets_after_success(monkeypatch):
    """A successful connect() resets the backoff so a later outage starts from base."""
    handler = ScriptedConnectBrokerHandler(
        queue_name="broker",
        error_handler=lambda record, exc: None,
        outcomes=["fail", "fail", "ok"],  # then fail once the script is exhausted
        send_error=RuntimeError("send failed"),
    )

    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *_args, **_kwargs):
        delays.append(delay)
        await real_sleep(0.001)

    monkeypatch.setattr(asyncio, "sleep", recording_sleep)
    monkeypatch.setattr(random, "uniform", lambda a, b: (a + b) / 2)

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while len(delays) < 3 and loop.time() < deadline:
        await real_sleep(0.01)

    await handler.stop_logging(timeout=0.5)

    assert len(delays) >= 3
    # fail (0.5) -> fail (1.0) -> success -> send fails -> reconnect fail is base again.
    assert delays[0] == pytest.approx(0.5)
    assert delays[1] == pytest.approx(1.0)
    assert delays[2] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_send_failure_surfaces_and_acks_record():
    """Send failures are reported and the queue task is acknowledged (dropped)."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler._send_error = RuntimeError("send failed")
    counting_queue = CountingQueue()
    handler.log_queues["broker"] = counting_queue

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(errors))
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert handler.send_attempts  # send_message was attempted
    # The failed record is acknowledged so the queue can drain; the drop is
    # surfaced via the error channel, not by poisoning the drain counter.
    assert counting_queue.task_done_calls == 1
    assert counting_queue.empty()

    await handler.stop_logging(timeout=0.5)


def test_base_broker_unknown_kwarg_raises_type_error():
    """A typo'd kwarg on the broker machinery fails loudly."""
    with pytest.raises(TypeError):
        FakeBrokerHandler(queue_name="broker", unknown_kwarg=True)


def test_broker_handler_is_abstract():
    """AsyncBrokerHandler and subclasses missing abstract methods cannot be instantiated."""
    with pytest.raises(TypeError):
        AsyncBrokerHandler(queue_name="broker")

    class MissingSendMessage(AsyncBrokerHandler):
        async def connect(self) -> None: ...

        async def disconnect(self) -> None: ...

    with pytest.raises(TypeError):
        MissingSendMessage(queue_name="broker")


@pytest.mark.asyncio
async def test_broker_output_deterministic_without_console():
    """Broker fields are computed independently of console formatting."""
    handler = FakeBrokerHandler(
        queue_name="broker",
    )

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(handler.sent))
    entry = handler.sent[0]
    assert entry["level"] == "INF"
    assert entry["name"] == "TestLogger"

    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_drain_returns_completed_result():
    """drain() returns a COMPLETED BackendDrainResult when the queue drains."""
    handler = FakeBrokerHandler(queue_name="broker")

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: bool(handler.sent))

    result = await handler.drain(timeout=0.5)

    assert result.name == "broker"
    assert result.status is DrainStatus.COMPLETED

    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_drain_returns_timeout_result():
    """drain() returns a TIMEOUT result when the queue does not drain in time."""
    handler = FakeBrokerHandler(queue_name="broker")
    handler.log_queues["broker"].put_nowait(_make_record("stuck"))  # no worker: never acked

    result = await handler.drain(timeout=0.01)

    assert result.name == "broker"
    assert result.status is DrainStatus.TIMEOUT


@pytest.mark.asyncio
async def test_drain_returns_error_result(monkeypatch):
    """drain() returns an ERROR result carrying the exception on join() failure."""
    handler = FakeBrokerHandler(queue_name="broker")

    async def failing_join():
        raise RuntimeError("join failed")

    monkeypatch.setattr(handler.log_queues["broker"], "join", failing_join)

    result = await handler.drain(timeout=0.5)

    assert result.name == "broker"
    assert result.status is DrainStatus.ERROR
    assert isinstance(result.error, RuntimeError)


_BROKER_CASES = [
    pytest.param(
        (
            AsyncRedisHandler,
            RedisConfig,
            {"stream_name": "s"},
            "redis_config",
            {"host": "example.com", "port": 7000, "db": 2},
        ),
        id="redis",
    ),
    pytest.param(
        (
            AsyncValkeyHandler,
            ValkeyConfig,
            {"stream_name": "s"},
            "valkey_config",
            {"addresses": [("example.com", 7000)]},
        ),
        id="valkey",
    ),
    pytest.param(
        (
            AsyncMqttHandler,
            MqttConfig,
            {"topic": "s"},
            "mqtt_config",
            {"host": "example.com", "port": 8883},
        ),
        id="mqtt",
    ),
]


@pytest.mark.parametrize("case", _BROKER_CASES)
def test_broker_config_is_typed(case):
    """A config dict becomes a typed backend_config with a matching client_config view."""
    handler_cls, config_cls, name_kwargs, config_kwarg, config_dict = case
    handler = handler_cls(**name_kwargs, **{config_kwarg: config_dict})

    assert handler.backend_config == config_cls(**config_dict)
    assert handler.client_config == asdict(config_cls(**config_dict))


@pytest.mark.parametrize("case", _BROKER_CASES)
def test_broker_config_defaults(case):
    """Omitting the config dict falls back to the typed config's own defaults."""
    handler_cls, config_cls, name_kwargs, _config_kwarg, _config_dict = case
    handler = handler_cls(**name_kwargs)

    assert handler.backend_config == config_cls()
    assert handler.client_config == asdict(config_cls())


@pytest.mark.parametrize("case", _BROKER_CASES)
def test_broker_unknown_kwarg_raises_type_error(case):
    """A typo'd kwarg on any concrete broker handler fails loudly."""
    handler_cls, _config_cls, name_kwargs, _config_kwarg, _config_dict = case
    with pytest.raises(TypeError):
        handler_cls(**name_kwargs, unknown_kwarg=True)


@pytest.mark.parametrize("case", _BROKER_CASES)
@pytest.mark.asyncio
async def test_broker_send_message_raises_when_not_connected(case):
    """send_message() raises instead of silently acking when no client is connected."""
    handler_cls, _config_cls, name_kwargs, _config_kwarg, _config_dict = case
    handler = handler_cls(**name_kwargs)
    record = {"level": "INF", "message": "m", "name": "n", "time": "t"}

    with pytest.raises(RuntimeError):
        await handler.send_message(record)
