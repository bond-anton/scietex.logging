"""Tests for the bounded queue overflow policy (AR-007)."""

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


@pytest.mark.asyncio
async def test_overflow_drops_record_and_reports_via_error_channel():
    """A full backend queue drops the record and reports it through the error handler."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        queue_maxsize=1,
        error_handler=lambda record, exc: errors.append(exc),
    )
    # Emit without starting the worker so the bounded queue fills and overflows.
    handler._loop = asyncio.get_running_loop()
    handler.logging_accept_event.set()

    handler.emit(_make_record("accepted"))
    handler.emit(_make_record("dropped"))

    # The first record fills the queue; the second is dropped, not buffered.
    assert handler.log_queues["broker"].qsize() == 1
    assert len(errors) == 1
    assert isinstance(errors[0], asyncio.QueueFull)


@pytest.mark.asyncio
async def test_overflow_reported_exception_is_exactly_queue_full():
    """The overflow surfaces as asyncio.QueueFull, not a generic wrapped exception."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        queue_maxsize=1,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler._loop = asyncio.get_running_loop()
    handler.logging_accept_event.set()

    handler.emit(_make_record("accepted"))
    handler.emit(_make_record("dropped"))

    assert len(errors) == 1
    assert type(errors[0]) is asyncio.QueueFull


@pytest.mark.asyncio
async def test_bounded_queue_drains_fully_at_stop():
    """A queue filled to capacity drains completely and delivers every accepted record."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        queue_maxsize=2,
    )

    await handler.start_logging()
    handler.emit(_make_record("a"))
    handler.emit(_make_record("b"))
    await handler.stop_logging(timeout=0.5)

    assert handler.log_queues["broker"].empty()
    assert [entry["message"] for entry in handler.sent] == ["a", "b"]


@pytest.mark.asyncio
async def test_restart_after_overflow_delivers_normally():
    """Overflow in one cycle does not corrupt state; a restart delivers normally."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        queue_maxsize=1,
        error_handler=lambda record, exc: errors.append(exc),
    )

    # Cycle 1: worker not started, so the queue fills and overflow drops a record.
    handler._loop = asyncio.get_running_loop()
    handler.logging_accept_event.set()
    handler.emit(_make_record("accepted"))
    handler.emit(_make_record("dropped"))
    assert handler.log_queues["broker"].qsize() == 1
    assert len(errors) == 1

    # Cycle 2: start the worker, emit, stop. The leftover record drains first,
    # then the new record is delivered, proving the handler is still restartable.
    await handler.start_logging()
    await _wait_for(lambda: len(handler.sent) == 1)
    handler.emit(_make_record("recovered"))
    await _wait_for(lambda: len(handler.sent) == 2)
    await handler.stop_logging(timeout=0.5)

    assert handler.log_queues["broker"].empty()
    assert [entry["message"] for entry in handler.sent] == ["accepted", "recovered"]


@pytest.mark.asyncio
async def test_console_drain_does_not_hang_when_queue_full():
    """Shutdown returns when the console queue is full and its worker is stalled."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        queue_maxsize=2,
    )

    # Replace the console worker with one that never drains, so the console
    # queue fills and stays full for the whole shutdown.
    async def stalled_worker() -> None:
        while handler.logging_running_event.is_set():
            await asyncio.sleep(0.01)

    handler.log_worker_factories[0] = stalled_worker

    await handler.start_logging()
    handler.emit(_make_record("one"))
    handler.emit(_make_record("two"))

    # The console queue is full; its drain must still return (status records are
    # dropped and queue.join is bounded by the timeout) instead of deadlocking.
    await asyncio.wait_for(handler.stop_logging(timeout=0.05), timeout=5)

    # The broker still delivered both records; the stalled console kept its queue.
    assert [entry["message"] for entry in handler.sent] == ["one", "two"]
    assert handler.log_queues["console"].qsize() == 2


def test_queue_maxsize_reaches_console_queue():
    """queue_maxsize bounds the console backend's queue."""
    handler = AsyncBaseHandler(queue_maxsize=5)
    assert handler.log_queues["console"].maxsize == 5


def test_queue_maxsize_reaches_broker_queue():
    """queue_maxsize bounds the broker backend's queue."""
    handler = FakeBrokerHandler(queue_name="broker", stdout_enable=False, queue_maxsize=5)
    assert handler.log_queues["broker"].maxsize == 5


def test_queue_maxsize_default_is_10000():
    """The default queue bound is 10000 for both console and broker backends."""
    assert AsyncBaseHandler().log_queues["console"].maxsize == 10000
    assert (
        FakeBrokerHandler(queue_name="broker", stdout_enable=False).log_queues["broker"].maxsize
        == 10000
    )
