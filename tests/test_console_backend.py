"""Tests for the ConsoleBackend console sink in isolation."""

import asyncio
import logging
import threading

import pytest

from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus
from scietex.logging.backend.console import ConsoleBackend


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


class FakeFormatter(logging.Formatter):
    """Formatter producing deterministic, timestamp-free output for tests."""

    def format(self, record: logging.LogRecord) -> str:
        return f"FMT:{record.getMessage()}"


class ExplodingFormatter(logging.Formatter):
    """Formatter that raises on format, proving the worker survives format errors."""

    def format(self, record: logging.LogRecord) -> str:
        raise RuntimeError("format exploded")


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
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)

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
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)
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
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("a"))
    await backend.queue.put(_make_record("b"))
    running_event.clear()

    await asyncio.wait_for(worker, timeout=5)

    assert worker.done()
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_report_status_queues_synthetic_records(capsys):
    """report_status() surfaces every backend's outcome as a synthetic status record."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)
    worker = asyncio.create_task(backend._worker())

    results = [
        BackendDrainResult(name="_console", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="_redis", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="_valkey", status=DrainStatus.TIMEOUT),
        BackendDrainResult(name="broker", status=DrainStatus.ERROR, error=RuntimeError("boom")),
    ]
    await backend.report_status(results)
    # Let the worker flush the status records before capturing stdout.
    await backend.queue.join()

    captured = capsys.readouterr().out
    # The console's own result is included alongside the other backends' outcomes.
    assert "Console Logger has completed processing its queue." in captured
    assert "Redis Logger has completed processing its queue." in captured
    assert "Timeout while waiting for valkey logger to complete its queue." in captured
    assert "Error while waiting for broker Logger: boom" in captured

    running_event.clear()
    await worker


@pytest.mark.asyncio
async def test_drain_returns_console_completed_result():
    """drain() drains the console queue and returns the console's own COMPLETED outcome."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)
    worker = asyncio.create_task(backend._worker())

    await backend.queue.put(_make_record("drain me"))
    result = await backend.drain(timeout=5)

    assert result.name == "_console"
    assert result.status is DrainStatus.COMPLETED

    running_event.clear()
    await worker


@pytest.mark.asyncio
async def test_drain_reports_timeout_when_queue_not_drained():
    """drain() reports TIMEOUT when the console queue does not drain in time."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)
    backend.queue.put_nowait(_make_record("stuck"))  # no worker: never acknowledged

    result = await backend.drain(timeout=0.01)

    assert result.name == "_console"
    assert result.status is DrainStatus.TIMEOUT


@pytest.mark.asyncio
async def test_worker_survives_format_error_and_reports():
    """A formatter failure is reported via the error channel, not a silent worker death."""
    errors = []
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(
        lambda: ExplodingFormatter(),
        running_event,
        error_handler=lambda record, exc: errors.append(exc),
    )

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("boom"))
    await _wait_for(lambda: bool(errors))
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    # The failed record is still acknowledged so the queue drains cleanly.
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_worker_reports_via_module_logger_when_no_error_handler(caplog):
    """Without an injected handler, a format failure is logged to the module logger."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: ExplodingFormatter(), running_event)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("boom"))

    with caplog.at_level(logging.ERROR, logger="scietex.logging"):
        await _wait_for(lambda: "failed to deliver a log record" in caplog.text)

    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert any("format exploded" in record.message for record in caplog.records)
    assert backend.queue.empty()


def test_report_error_helper_routes_to_handler_or_module_logger(caplog):
    """The shared report_error helper honors the error_handler and falls back (AR-108)."""
    from scietex.logging.config import report_error

    calls = []
    report_error("ConsoleBackend", lambda r, e: calls.append(e), None, RuntimeError("boom"))
    assert len(calls) == 1

    def bad(record, exc):
        raise RuntimeError("handler broken")

    with caplog.at_level(logging.ERROR, logger="scietex.logging"):
        report_error("ConsoleBackend", bad, None, RuntimeError("boom"))
    assert any("failed to deliver a log record" in r.getMessage() for r in caplog.records)


def test_worker_property_exposes_bound_worker():
    """worker is a public read-only accessor for the worker coroutine (AR-115)."""
    running_event = asyncio.Event()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)

    assert backend.worker.__func__ is ConsoleBackend._worker  # same underlying coroutine
    assert backend.worker.__self__ is backend  # bound to this instance
    assert callable(backend.worker)
    with pytest.raises(AttributeError):
        backend.worker = None  # read-only


@pytest.mark.asyncio
async def test_worker_writes_off_the_event_loop_thread(capsys):
    """The blocking stdout write runs on a worker thread, not the loop thread."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)
    loop_thread = threading.current_thread().name

    # Patch sys.stdout with a recorder that captures the writing thread.
    import sys

    class ThreadRecordingStream:
        def __init__(self):
            self.threads = []
            self.buf = []

        def write(self, text):
            self.threads.append(threading.current_thread().name)
            self.buf.append(text)

        def flush(self):
            pass

    recorder = ThreadRecordingStream()
    original = sys.stdout
    sys.stdout = recorder
    try:
        worker = asyncio.create_task(backend._worker())
        await backend.queue.put(_make_record("off loop"))
        running_event.clear()
        await asyncio.wait_for(worker, timeout=5)
    finally:
        sys.stdout = original

    assert "".join(recorder.buf) == "FMT:off loop\n"
    assert recorder.threads and all(t != loop_thread for t in recorder.threads)
