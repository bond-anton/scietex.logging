"""Tests for the FileBackend file sink in isolation."""

import asyncio
import logging

import pytest

from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus
from scietex.logging.backend.file import FileBackend, RotatingFileBackend


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


@pytest.mark.asyncio
async def test_worker_writes_formatted_record_to_file(tmp_path):
    """The backend worker drains its queue and writes formatted records to the file."""
    running_event = asyncio.Event()
    running_event.set()
    path = tmp_path / "x.log"
    backend = FileBackend(lambda: FakeFormatter(), running_event, filename=str(path))

    worker = asyncio.create_task(backend.worker())
    await backend.queue.put(_make_record("hello file"))
    running_event.clear()  # drain the remaining record, then wind down

    await asyncio.wait_for(worker, timeout=5)

    assert "FMT:hello file\n" in path.read_text()
    assert backend.queue.empty()


@pytest.mark.asyncio
async def test_base_worker_passes_the_original_record_to_write_record(tmp_path):
    """The plain backend hands _write_record the original record (no copy)."""
    captured: list[logging.LogRecord | None] = []
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(lambda: FakeFormatter(), running_event, filename=str(tmp_path / "x.log"))

    def spy_write(text: str, record: logging.LogRecord | None = None) -> None:
        captured.append(record)

    backend._write_record = spy_write  # noqa: SLF001
    original = _make_record("same me")
    worker = asyncio.create_task(backend.worker())
    await backend.queue.put(original)
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert len(captured) == 1
    assert captured[0] is original


@pytest.mark.asyncio
async def test_rotating_worker_passes_a_record_copy_to_write_record(tmp_path):
    """The rotating worker hands _write_record a copy, not the shared record."""
    captured: list[logging.LogRecord | None] = []
    running_event = asyncio.Event()
    running_event.set()
    backend = RotatingFileBackend(
        lambda: FakeFormatter(), running_event, filename=str(tmp_path / "rot.log")
    )

    def spy_write(text: str, record: logging.LogRecord | None = None) -> None:
        captured.append(record)

    backend._write_record = spy_write  # noqa: SLF001
    original = _make_record("copy me")
    worker = asyncio.create_task(backend.worker())
    await backend.queue.put(original)
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert len(captured) == 1
    written = captured[0]
    assert written is not None
    assert written is not original  # a copy, not the shared record
    assert written.getMessage() == original.getMessage()


@pytest.mark.asyncio
async def test_report_status_queues_synthetic_records():
    """report_status() enqueues a synthetic status record for each backend outcome."""
    backend = FileBackend(lambda: FakeFormatter(), asyncio.Event())

    results = [
        BackendDrainResult(name="_file", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="_redis", status=DrainStatus.TIMEOUT),
    ]
    await backend.report_status(results)

    records = [backend.queue.get_nowait() for _ in results]
    assert backend.queue.empty()
    assert "File Logger has completed processing its queue." in records[0].getMessage()
    assert (
        "Timeout while waiting for Redis logger to complete its queue." in records[1].getMessage()
    )


@pytest.mark.asyncio
async def test_drain_returns_file_completed_result():
    """drain() returns COMPLETED once every queued record is acknowledged."""
    backend = FileBackend(lambda: FakeFormatter(), asyncio.Event())

    await backend.queue.put(_make_record("drain me"))
    # Acknowledge the record on the handler's behalf so queue.join() returns.
    backend.queue.get_nowait()
    backend.queue.task_done()

    result = await backend.drain(timeout=5)

    assert result.name == "_file"
    assert result.status is DrainStatus.COMPLETED


@pytest.mark.asyncio
async def test_drain_reports_timeout_when_queue_not_drained():
    """drain() reports TIMEOUT when the file queue does not drain in time."""
    running_event = asyncio.Event()
    running_event.set()
    backend = FileBackend(lambda: FakeFormatter(), running_event)
    backend.queue.put_nowait(_make_record("stuck"))  # no worker: never acknowledged

    result = await backend.drain(timeout=0.01)

    assert result.name == "_file"
    assert result.status is DrainStatus.TIMEOUT
