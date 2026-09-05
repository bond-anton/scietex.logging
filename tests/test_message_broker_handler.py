"""Tests for AsyncBrokerHandler abstract base class and its worker."""

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


class CountingQueue(asyncio.Queue):
    """Queue that records how many times task_done() is called."""

    def __init__(self):
        super().__init__()
        self.task_done_calls = 0

    def task_done(self):
        self.task_done_calls += 1
        super().task_done()


class FakeBrokerHandler(AsyncBrokerHandler):
    """Concrete broker handler recording connect/send activity for tests."""

    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, str]] = []
        self.send_attempts: list[dict[str, str]] = []
        self.connect_attempts = 0
        self.connect_failures = 0
        self._send_error: Exception | None = None
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("connect failed")
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        self.send_attempts.append(record)
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(record)


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


@pytest.mark.asyncio
async def test_stop_logging_cancels_stuck_connect_worker():
    """stop_logging returns instead of deadlocking when connect() outlives the timeout."""
    handler = StuckConnectBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
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
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
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
async def test_send_failure_surfaces_and_acks_record():
    """Send failures are reported and the queue task is acknowledged (dropped)."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
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


def test_broker_unknown_kwarg_raises_type_error():
    """A typo'd kwarg on a broker handler fails loudly."""
    with pytest.raises(TypeError):
        FakeBrokerHandler(queue_name="broker", stdout_enabel=True)


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
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
    )

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(handler.sent))
    entry = handler.sent[0]
    assert entry["level"] == "INF"
    assert entry["name"] == "TestService:1"

    await handler.stop_logging(timeout=0.5)
