"""Tests for AsyncFileHandler and its rotation variants."""

import io
import logging
import os

import pytest

from scietex.logging.file_handler import (
    AsyncFileHandler,
    AsyncRotatingFileHandler,
    AsyncTimedRotatingFileHandler,
    AsyncWatchedFileHandler,
)
from scietex.logging.json_formatter import JsonFormatter


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


@pytest.mark.asyncio
async def test_file_handler_registers_file_backend(tmp_path):
    """AsyncFileHandler registers a 'file' backend like AsyncBaseHandler registers console."""
    path = tmp_path / "app.log"
    handler = AsyncFileHandler(str(path), service_name="TestService", worker_id=1)
    await handler.start_logging()

    assert "file" in handler.log_queues
    assert handler._file_backend is not None
    assert handler.log_queues["file"] is handler._file_backend.queue

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_file_handler_writes_to_file(tmp_path):
    """Records are formatted and written to the file."""
    path = tmp_path / "app.log"
    handler = AsyncFileHandler(str(path), service_name="TestService", worker_id=1)
    await handler.start_logging()

    logger = logging.getLogger("FileTestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("hello file")
    await handler.stop_logging()

    content = path.read_text()
    assert "hello file" in content


@pytest.mark.asyncio
async def test_file_handler_appends_by_default(tmp_path):
    """mode='a' (the default) appends to an existing file."""
    path = tmp_path / "app.log"
    path.write_text("preexisting\n")
    handler = AsyncFileHandler(str(path), service_name="TestService", worker_id=1)
    await handler.start_logging()

    logger = logging.getLogger("FileTestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("appended")
    await handler.stop_logging()

    content = path.read_text()
    assert content.startswith("preexisting\n")
    assert "appended" in content


@pytest.mark.asyncio
async def test_file_handler_json_formatter(tmp_path):
    """A JsonFormatter produces one JSON object per line in the file."""
    path = tmp_path / "app.jsonl"
    handler = AsyncFileHandler(
        str(path),
        service_name="TestService",
        worker_id=1,
        formatter=JsonFormatter(),
    )
    await handler.start_logging()

    logger = logging.getLogger("JsonFileLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("json message")
    await handler.stop_logging()

    import json

    lines = [line for line in path.read_text().splitlines() if line.strip()]
    parsed = json.loads(lines[0])
    assert parsed["message"] == "json message"
    assert parsed["level"] == "INFO"


@pytest.mark.asyncio
async def test_file_handler_injected_file_like_not_closed():
    """An injected file-like is written to but never closed by the handler."""
    stream = io.StringIO()
    handler = AsyncFileHandler(
        service_name="TestService",
        worker_id=1,
        file=stream,
        stdout_enable=False,
    )
    await handler.start_logging()

    logger = logging.getLogger("InjectedFileLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("injected write")
    await handler.stop_logging()

    assert "injected write" in stream.getvalue()
    assert not stream.closed  # the caller owns the injected file-like


def test_file_and_filename_mutually_exclusive(tmp_path):
    """Passing both file= and filename= raises ValueError."""
    with pytest.raises(ValueError):
        AsyncFileHandler(str(tmp_path / "x.log"), file=io.StringIO())


@pytest.mark.asyncio
async def test_rotating_file_handler_rolls_over(tmp_path):
    """AsyncRotatingFileHandler rolls over when maxBytes is exceeded."""
    path = tmp_path / "rot.log"
    handler = AsyncRotatingFileHandler(
        str(path),
        service_name="TestService",
        worker_id=1,
        maxBytes=100,
        backupCount=2,
        stdout_enable=False,
    )
    await handler.start_logging()

    logger = logging.getLogger("RotatingLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    for i in range(50):
        logger.info("line %d with enough padding to exceed the byte cap", i)
    await handler.stop_logging()

    # Rollover produced at least one backup file.
    backups = [p for p in os.listdir(tmp_path) if p.startswith("rot.log.")]
    assert len(backups) >= 1


@pytest.mark.asyncio
async def test_timed_rotating_file_handler_writes(tmp_path):
    """AsyncTimedRotatingFileHandler writes records (rollover is time-driven)."""
    path = tmp_path / "timed.log"
    handler = AsyncTimedRotatingFileHandler(
        str(path),
        service_name="TestService",
        worker_id=1,
        when="S",
        interval=1,
        backupCount=2,
        stdout_enable=False,
    )
    await handler.start_logging()

    logger = logging.getLogger("TimedLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("timed message")
    await handler.stop_logging()

    assert "timed message" in path.read_text()


@pytest.mark.asyncio
async def test_watched_file_handler_writes(tmp_path):
    """AsyncWatchedFileHandler writes records to the watched file."""
    path = tmp_path / "watched.log"
    handler = AsyncWatchedFileHandler(str(path), service_name="TestService", worker_id=1)
    await handler.start_logging()

    logger = logging.getLogger("WatchedLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("watched message")
    await handler.stop_logging()

    assert "watched message" in path.read_text()
