"""Tests for the FileBackend file sink in isolation."""

import asyncio

import pytest
from conftest import FakeFormatter, _make_record

from scietex.logging.backend.file import FileBackend


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
