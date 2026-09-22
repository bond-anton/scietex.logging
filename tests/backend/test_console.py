"""Tests for the ConsoleBackend console sink in isolation."""

import asyncio
import logging
import threading

import pytest
from conftest import ExplodingFormatter, FakeFormatter, _make_record, _wait_for

from scietex.logging.backend.console import ConsoleBackend


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
async def test_worker_exits_when_running_clears_and_queue_empty():
    """The worker terminates (does not hang) once logging stops and the queue drains."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("a"))
    await backend.queue.put(_make_record("b"))
    running_event.clear()

    # wait_for returning proves the worker terminated instead of hanging; the
    # empty queue proves it drained every record on the way out.
    await asyncio.wait_for(worker, timeout=5)

    assert backend.queue.empty()


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
