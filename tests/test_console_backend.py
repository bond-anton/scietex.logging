"""Tests for the ConsoleBackend console sink in isolation."""

import asyncio
import logging

import pytest

from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus
from scietex.logging.console_backend import ConsoleBackend


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


class FakeFormatter(logging.Formatter):
    """Formatter producing deterministic, timestamp-free output for tests."""

    def format(self, record: logging.LogRecord) -> str:
        return f"FMT:{record.getMessage()}"


class CountingQueue(asyncio.Queue):
    """Queue that records how many times task_done() is called."""

    def __init__(self) -> None:
        super().__init__()
        self.task_done_calls = 0

    def task_done(self) -> None:
        self.task_done_calls += 1
        super().task_done()


@pytest.mark.asyncio
async def test_worker_writes_formatted_record_to_stdout(capsys):
    """The worker drains its queue and writes formatted records to stdout."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(FakeFormatter(), running_event)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("hello console"))
    running_event.clear()  # drain the remaining record, then wind down

    await asyncio.wait_for(worker, timeout=5)

    assert "FMT:hello console" in capsys.readouterr().out
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_worker_calls_task_done_for_each_record():
    """Every record drained by the worker is acknowledged via task_done()."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(FakeFormatter(), running_event)
    counting_queue = CountingQueue()
    backend.queue = counting_queue

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("first"))
    await backend.queue.put(_make_record("second"))
    running_event.clear()

    await asyncio.wait_for(worker, timeout=5)

    assert counting_queue.task_done_calls == 2
    assert counting_queue.empty()


@pytest.mark.asyncio
async def test_worker_exits_when_running_clears_and_queue_empty():
    """The worker terminates (does not hang) once logging stops and the queue drains."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(FakeFormatter(), running_event)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("a"))
    await backend.queue.put(_make_record("b"))
    running_event.clear()

    await asyncio.wait_for(worker, timeout=5)

    assert worker.done()
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_drain_queues_status_records_for_each_outcome(capsys):
    """drain() surfaces every other backend's outcome as a synthetic status record."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(FakeFormatter(), running_event)
    worker = asyncio.create_task(backend._worker())

    results = [
        BackendDrainResult(name="redis", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="valkey", status=DrainStatus.TIMEOUT),
        BackendDrainResult(name="broker", status=DrainStatus.ERROR, error=RuntimeError("boom")),
    ]
    await backend.drain(timeout=5, results=results)

    captured = capsys.readouterr().out
    assert "Redis Logger has completed processing its queue." in captured
    assert "Timeout while waiting for valkey logger to complete its queue." in captured
    assert "Error while waiting for broker Logger: boom" in captured

    running_event.clear()
    await worker
