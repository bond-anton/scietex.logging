"""Tests for the shared _QueueBackend drain/report_status machinery.

The drain and status-reporting surface lives once on the base
(``backend/_base.py``); the concrete peer backends only differ in their
``_queue_name`` / ``_backend_name`` labels. These tests exercise that shared
surface directly so the per-backend files do not each re-test it.
"""

import asyncio

import pytest
from conftest import FakeFormatter, _make_record

from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus
from scietex.logging.backend.console import ConsoleBackend
from scietex.logging.backend.file import FileBackend


class _StubBackend(ConsoleBackend):
    """Minimal concrete backend for exercising the inherited drain surface."""

    _queue_name = "_stub"
    _backend_name = "StubBackend"


def _make_backend(cls, **kwargs):
    running_event = asyncio.Event()
    running_event.set()
    return cls(lambda: FakeFormatter(), running_event, **kwargs)


@pytest.mark.asyncio
async def test_drain_returns_completed_result():
    """drain() returns COMPLETED once every queued record is acknowledged."""
    backend = _make_backend(_StubBackend)

    await backend.queue.put(_make_record("drain me"))
    # Acknowledge the record on the worker's behalf so queue.join() returns.
    backend.queue.get_nowait()
    backend.queue.task_done()

    result = await backend.drain(timeout=5)

    assert result.name == "_stub"
    assert result.status is DrainStatus.COMPLETED


@pytest.mark.asyncio
async def test_drain_reports_timeout_when_queue_not_drained():
    """drain() reports TIMEOUT when the queue does not drain in time."""
    backend = _make_backend(_StubBackend)
    backend.queue.put_nowait(_make_record("stuck"))  # no worker: never acknowledged

    result = await backend.drain(timeout=0.01)

    assert result.name == "_stub"
    assert result.status is DrainStatus.TIMEOUT


@pytest.mark.asyncio
async def test_drain_returns_error_result(monkeypatch):
    """drain() returns an ERROR result carrying the exception on join() failure."""
    backend = _make_backend(_StubBackend)

    async def failing_join():
        raise RuntimeError("join failed")

    monkeypatch.setattr(backend.queue, "join", failing_join)

    result = await backend.drain(timeout=0.5)

    assert result.name == "_stub"
    assert result.status is DrainStatus.ERROR
    assert isinstance(result.error, RuntimeError)


@pytest.mark.parametrize(
    ("backend_cls", "queue_name", "label"),
    [
        pytest.param(ConsoleBackend, "_console", "Console", id="console"),
        pytest.param(FileBackend, "_file", "File", id="file"),
    ],
)
@pytest.mark.asyncio
async def test_report_status_queues_synthetic_records(backend_cls, queue_name, label):
    """report_status() enqueues a synthetic status record per backend outcome."""
    backend = _make_backend(backend_cls)

    results = [
        BackendDrainResult(name=queue_name, status=DrainStatus.COMPLETED),
        BackendDrainResult(name="_redis", status=DrainStatus.TIMEOUT),
        BackendDrainResult(name="broker", status=DrainStatus.ERROR, error=RuntimeError("boom")),
    ]
    await backend.report_status(results)

    records = [backend.queue.get_nowait() for _ in results]
    assert backend.queue.empty()
    assert f"{label} Logger has completed processing its queue." in records[0].getMessage()
    assert (
        "Timeout while waiting for Redis logger to complete its queue." in records[1].getMessage()
    )
    assert "Error while waiting for Broker Logger: boom" in records[2].getMessage()
