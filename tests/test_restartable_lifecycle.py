"""Restartability tests for the AsyncLoggingHandler start/stop lifecycle."""

import asyncio
import logging

import pytest

from scietex.logging import AsyncBaseHandler
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


class FakeBrokerHandler(AsyncBrokerHandler):
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


class FlakyBrokerHandler(AsyncBrokerHandler):
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


@pytest.mark.asyncio
async def test_console_start_stop_start_cycle(capsys):
    """A console handler can be started, stopped, and started again on one loop."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)

    await handler.start_logging()
    handler.emit(_make_record("first"))
    await handler.stop_logging()
    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()
    assert handler.log_queues["console"].empty()

    await handler.start_logging()
    handler.emit(_make_record("second"))
    await handler.stop_logging()

    captured = capsys.readouterr().out
    assert "first" in captured
    assert "second" in captured
    assert handler.log_queues["console"].empty()


@pytest.mark.asyncio
async def test_broker_start_stop_start_cycle():
    """Each start reconnects, and each stop disconnects and clears the client."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
    )

    await handler.start_logging()
    handler.emit(_make_record("first"))
    await _wait_for(lambda: len(handler.sent) == 1)
    await handler.stop_logging(timeout=0.5)
    assert handler.client is None
    assert handler.connect_attempts == 1

    await handler.start_logging()
    handler.emit(_make_record("second"))
    await _wait_for(lambda: len(handler.sent) == 2)
    await handler.stop_logging(timeout=0.5)
    assert handler.client is None
    assert handler.connect_attempts == 2

    assert handler.sent[0]["message"] == "first"
    assert handler.sent[1]["message"] == "second"


@pytest.mark.asyncio
async def test_mixed_handler_start_stop_start_cycle(capsys):
    """Console and broker both deliver records across two full cycles."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
    )

    await handler.start_logging()
    handler.emit(_make_record("mixed-first"))
    await _wait_for(lambda: len(handler.sent) == 1)
    await handler.stop_logging(timeout=1)
    assert handler.client is None

    await handler.start_logging()
    handler.emit(_make_record("mixed-second"))
    await _wait_for(lambda: len(handler.sent) == 2)
    await handler.stop_logging(timeout=1)
    assert handler.client is None

    captured = capsys.readouterr().out
    assert "mixed-first" in captured
    assert "mixed-second" in captured
    assert handler.sent[0]["message"] == "mixed-first"
    assert handler.sent[1]["message"] == "mixed-second"


@pytest.mark.asyncio
async def test_double_start_raises():
    """start_logging is not re-entrant while running."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)

    await handler.start_logging()
    with pytest.raises(RuntimeError):
        await handler.start_logging()
    await handler.stop_logging()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop():
    """stop_logging on a fresh handler is a no-op that leaves events unset."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)

    await handler.stop_logging()

    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()
    assert handler.log_workers_tasks == []


@pytest.mark.asyncio
async def test_emit_during_gap_is_dropped():
    """Records emitted between stop and the next start reach no backend."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
    )

    await handler.start_logging()
    await handler.stop_logging(timeout=0.5)

    handler.emit(_make_record("dropped"))
    assert not handler.logging_accept_event.is_set()
    assert handler.log_queues["broker"].empty()
    assert handler.sent == []

    await handler.start_logging()
    handler.emit(_make_record("kept"))
    await _wait_for(lambda: len(handler.sent) == 1)
    assert handler.sent[0]["message"] == "kept"
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_send_failure_acks_and_restart_recovers(capsys):
    """A send failure is acked (no false drain timeout); a restart recovers."""
    errors = []
    handler = FlakyBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.failures_before_success = 2  # first two sends fail, then succeed

    await handler.start_logging()
    handler.emit(_make_record("one"))
    handler.emit(_make_record("two"))
    handler.emit(_make_record("three"))
    await _wait_for(lambda: len(handler.sent) == 1)
    assert handler.sent[0]["message"] == "three"
    assert len(errors) == 2

    # The failed sends were acked, so the drain reports COMPLETED, not a false
    # TIMEOUT, and stop_logging returns promptly.
    await handler.stop_logging(timeout=0.5)
    assert handler.client is None
    captured = capsys.readouterr().out
    assert "Broker Logger has completed processing its queue." in captured
    assert "Timeout while waiting for broker" not in captured

    # A fresh start schedules a fresh worker that reconnects and delivers.
    sent_before = len(handler.sent)
    await handler.start_logging()
    handler.emit(_make_record("recovered"))
    await _wait_for(lambda: len(handler.sent) == sent_before + 1)
    assert handler.sent[-1]["message"] == "recovered"
    # connect() runs once for the initial start, once after each of the two send
    # failures (AR-015 resets the client so the next iteration reconnects), and
    # once more for the restart.
    assert handler.connect_attempts == 4
    await handler.stop_logging(timeout=0.5)
