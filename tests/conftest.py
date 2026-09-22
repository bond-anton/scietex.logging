"""Shared test helpers for the scietex.logging test suite.

These are plain module-level helpers, not pytest fixtures: each test module
imports the ones it needs via ``from conftest import ...``. Centralizing them
here removes the per-file duplication that accumulated as the suite grew.
"""

import asyncio
import io
import logging
import threading
import time

from scietex.logging.handler.broker import AsyncBrokerHandler


def _make_record(
    message: str = "test message",
    level: int = logging.INFO,
    exc_info=None,
) -> logging.LogRecord:
    """Build a deterministic LogRecord for exercising handlers and backends."""
    return logging.LogRecord(
        name="TestLogger",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=exc_info,
    )


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    """Poll ``predicate`` until it returns truthy, or raise TimeoutError."""
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


class FakeBrokerHandler(AsyncBrokerHandler):
    """Concrete broker handler recording connect/send activity for tests."""

    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, str]] = []
        self.send_attempts: list[dict[str, str]] = []
        self.connect_attempts = 0
        self.connect_failures = 0
        self._send_error: Exception | None = None
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("connect failed")
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        self.send_attempts.append(record)
        if self._send_error is not None:
            raise self._send_error
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


class RecordingStream:
    """File-like wrapper that records the thread each write runs on."""

    def __init__(self, inner, threads: list[str]) -> None:
        self.inner = inner
        self.threads = threads

    def write(self, text: str) -> int:
        self.threads.append(threading.current_thread().name)
        return self.inner.write(text)

    def flush(self) -> None:
        self.inner.flush()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.inner.seek(offset, whence)

    def tell(self) -> int:
        return self.inner.tell()

    def close(self) -> None:
        self.inner.close()


class RecordingStringIO(io.StringIO):
    """Injected file-like that records the thread each write runs on."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: list[str] = []

    def write(self, text: str) -> int:
        self.threads.append(threading.current_thread().name)
        return super().write(text)


class SlowStream:
    """File-like whose write blocks, recording start/finish for cancel tests."""

    def __init__(self, inner, state: dict) -> None:
        self.inner = inner
        self.state = state
        self.closed = False

    def write(self, text: str) -> int:
        self.state["write_started"].set()
        time.sleep(0.5)  # simulate a slow disk write
        self.inner.write(text)
        self.state["write_finished"].set()
        return len(text)

    def flush(self) -> None:
        self.inner.flush()

    def close(self) -> None:
        self.closed = True
        self.inner.close()
