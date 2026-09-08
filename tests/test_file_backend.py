"""Tests for the FileBackend file sink in isolation."""

import asyncio
import io
import logging

import pytest

from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus
from scietex.logging.backend.file import FileBackend


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


@pytest.mark.asyncio
async def test_worker_writes_off_the_event_loop_thread():
    """The blocking stream write runs on a worker thread, not the loop thread."""
    import threading

    running_event = asyncio.Event()
    running_event.set()
    loop_thread = threading.current_thread().name

    class ThreadRecordingStream:
        def __init__(self):
            self.threads = []
            self.buf = []

        def write(self, text):
            self.threads.append(threading.current_thread().name)
            self.buf.append(text)
            return len(text)

        def flush(self):
            pass

    stream = ThreadRecordingStream()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: stream)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("off loop"))
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert "".join(stream.buf) == "FMT:off loop\n"
    assert stream.threads and all(t != loop_thread for t in stream.threads)


@pytest.mark.asyncio
async def test_worker_writes_formatted_record_to_stream():
    """The worker drains its queue and writes formatted records to the stream."""
    running_event = asyncio.Event()
    running_event.set()
    stream = io.StringIO()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: stream)

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("hello file"))
    running_event.clear()  # drain the remaining record, then wind down

    await asyncio.wait_for(worker, timeout=5)

    assert "FMT:hello file\n" in stream.getvalue()
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_worker_reads_stream_provider_dynamically():
    """The stream is read at work time, so a lazily-opened handle is always current."""
    running_event = asyncio.Event()
    running_event.set()
    streams = [io.StringIO(), io.StringIO()]
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: streams[0])

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("first"))
    # Swap the stream mid-flight; the next record must go to the new stream.
    await _wait_for(lambda: "FMT:first" in streams[0].getvalue())
    streams[0], streams[1] = streams[1], streams[0]
    await backend.queue.put(_make_record("second"))
    running_event.clear()

    await asyncio.wait_for(worker, timeout=5)

    assert "FMT:second\n" in streams[0].getvalue()
    assert "FMT:second" not in streams[1].getvalue()


@pytest.mark.asyncio
async def test_worker_exits_when_running_clears_and_queue_empty():
    """The worker terminates (does not hang) once logging stops and the queue drains."""
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: io.StringIO())

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("a"))
    await backend.queue.put(_make_record("b"))
    running_event.clear()

    await asyncio.wait_for(worker, timeout=5)

    assert worker.done()
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_report_status_queues_synthetic_records():
    """report_status() surfaces every backend's outcome as a synthetic status record."""
    running_event = asyncio.Event()
    running_event.set()
    stream = io.StringIO()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: stream)
    worker = asyncio.create_task(backend._worker())

    results = [
        BackendDrainResult(name="_file", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="_redis", status=DrainStatus.TIMEOUT),
    ]
    await backend.report_status(results)
    # Let the worker flush the status records before reading the stream.
    await backend.queue.join()

    captured = stream.getvalue()
    assert "File Logger has completed processing its queue." in captured
    assert "Timeout while waiting for redis logger to complete its queue." in captured

    running_event.clear()
    await worker


@pytest.mark.asyncio
async def test_drain_returns_file_completed_result():
    """drain() drains the file queue and returns the file's own COMPLETED outcome."""
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: io.StringIO())
    worker = asyncio.create_task(backend._worker())

    await backend.queue.put(_make_record("drain me"))
    result = await backend.drain(timeout=5)

    assert result.name == "_file"
    assert result.status is DrainStatus.COMPLETED

    running_event.clear()
    await worker


@pytest.mark.asyncio
async def test_drain_reports_timeout_when_queue_not_drained():
    """drain() reports TIMEOUT when the file queue does not drain in time."""
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: io.StringIO())
    backend.queue.put_nowait(_make_record("stuck"))  # no worker: never acknowledged

    result = await backend.drain(timeout=0.01)

    assert result.name == "_file"
    assert result.status is DrainStatus.TIMEOUT


@pytest.mark.asyncio
async def test_worker_survives_format_error_and_reports():
    """A formatter failure is reported via the error channel, not a silent worker death."""
    errors = []
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(
        lambda: ExplodingFormatter(),
        running_event,
        lambda: io.StringIO(),
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
async def test_worker_survives_write_error_and_reports():
    """A broken stream write is reported via the error channel, not a silent worker death."""
    errors = []
    running_event = asyncio.Event()
    running_event.set()

    class BrokenStream:
        def write(self, text: str) -> int:
            raise OSError("disk full")

        def flush(self) -> None:
            pass

    backend = FileBackend(
        lambda: FakeFormatter(),
        running_event,
        lambda: BrokenStream(),
        error_handler=lambda record, exc: errors.append(exc),
    )

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("boom"))
    await _wait_for(lambda: bool(errors))
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert backend.queue.empty()


def test_worker_property_exposes_bound_worker():
    """worker is a public read-only accessor for the worker coroutine (AR-115)."""
    running_event = asyncio.Event()
    backend = FileBackend(lambda: FakeFormatter(), running_event, lambda: io.StringIO())

    assert backend.worker.__func__ is FileBackend._worker  # same underlying coroutine
    assert backend.worker.__self__ is backend  # bound to this instance
    assert callable(backend.worker)
    with pytest.raises(AttributeError):
        backend.worker = None  # read-only
