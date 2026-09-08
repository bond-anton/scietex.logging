"""Tests for ConsoleHandler class."""

import asyncio
import logging

import pytest

from scietex.logging import ConsoleHandler, ScietexFormatter
from scietex.logging.handler.broker import AsyncBrokerHandler
from scietex.logging.handler.file import AsyncFileHandler


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


class _NoopBrokerHandler(AsyncBrokerHandler):
    """Minimal broker that connects instantly and acknowledges every record."""

    async def connect(self) -> None:
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        pass


class _ExplodingFormatter(logging.Formatter):
    """Formatter that raises on format, simulating a broken stdout at the handler level."""

    def format(self, record: logging.LogRecord) -> str:
        raise RuntimeError("format exploded")


@pytest.mark.asyncio
async def test_console_handler_initialization():
    """Test the initialization of ConsoleHandler with default values."""
    handler = ConsoleHandler()
    await handler.start_logging()
    assert "_console" in handler.log_queues  # Console queue should be initialized by default
    await handler.stop_logging()


@pytest.mark.asyncio
async def test_start_and_stop_logging():
    """Test starting and stopping the logging process."""
    handler = ConsoleHandler()
    await handler.start_logging()

    # Ensure logging events are set
    assert handler.logging_accept_event.is_set()
    assert handler.logging_running_event.is_set()

    await handler.stop_logging()

    # Ensure logging events are cleared after stopping
    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()


@pytest.mark.asyncio
async def test_emit_logs_to_queue():
    """Test that log records are added to the appropriate queues."""
    handler = ConsoleHandler()
    await handler.start_logging()

    # Create a test log record
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    # Emit a log record
    logger.info("Test log message")

    # Ensure the log record was added to the console queue
    log_record = await asyncio.wait_for(handler.log_queues["_console"].get(), timeout=1)
    assert log_record.getMessage() == "Test log message"

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_console_worker_outputs_log(capsys):
    """Test that the console worker processes and outputs logs correctly."""
    handler = ConsoleHandler()

    await handler.start_logging()

    # Emit a test log record
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("Test log message")

    # Allow the console worker to process the message
    await asyncio.sleep(0.1)

    # Capture stdout output
    captured = capsys.readouterr()
    assert "Test log message" in captured.out

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_set_formatter_propagates_to_console_backend(capsys):
    """setFormatter must update the console backend so console output reflects it."""
    handler = ConsoleHandler()
    formatter = ScietexFormatter(
        fmt="%(levelname)s | %(message)s",
    )
    handler.setFormatter(formatter)

    # The console backend must now use the custom formatter, not the stale default.
    assert handler._console_backend is not None
    assert handler._console_backend.formatter_provider() is formatter

    await handler.start_logging()
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("Custom format message")
    await asyncio.sleep(0.1)

    captured = capsys.readouterr()
    # Custom format uses "|" separators and no timestamp prefix.
    assert "INF | Custom format message" in captured.out
    assert " - " not in captured.out.split("Custom format message")[0]

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_set_formatter_console_reads_dynamically(capsys):
    """setFormatter affects console output mid-stream without a manual backend re-sync."""
    handler = ConsoleHandler()
    await handler.start_logging()

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("before")
    handler.setFormatter(ScietexFormatter(fmt="%(levelname)s | %(message)s"))
    logger.info("after")
    await handler.stop_logging()

    captured = capsys.readouterr().out
    # The record logged after setFormatter is rendered with the new formatter.
    assert "INF | after" in captured


@pytest.mark.asyncio
async def test_console_write_failure_reported_and_shutdown_clean():
    """A console format/write failure is reported and stop_logging still completes."""
    errors = []
    handler = ConsoleHandler(
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.setFormatter(_ExplodingFormatter())
    await handler.start_logging()

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("boom")

    await handler.stop_logging(timeout=5)

    assert len(errors) >= 1
    assert all(isinstance(err, RuntimeError) for err in errors)
    assert handler.log_queues["_console"].empty()


@pytest.mark.asyncio
async def test_stop_logging_drains_queues():
    """Test that stop_logging waits for all queued records to be processed."""
    handler = ConsoleHandler()
    await handler.start_logging()

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    for i in range(5):
        logger.info("Test log message %d", i)

    await handler.stop_logging()

    # Records were queued synchronously and drained by the console worker. The
    # queue.join() inside stop_logging already guarantees every item was acknowledged.
    assert handler.log_queues["_console"].empty()


@pytest.mark.asyncio
async def test_emit_delivers_to_backend_queue():
    """emit writes to the ingress and the bridge delivers to the backend queue."""
    handler = ConsoleHandler()
    handler.log_queues["custom"] = asyncio.Queue()
    await handler.start_logging()

    record = logging.LogRecord("test", logging.INFO, "", 0, "sync message", None, None)
    handler.emit(record)

    got = await asyncio.wait_for(handler.log_queues["custom"].get(), timeout=1)
    assert got.getMessage() == "sync message"
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_emit_off_loop_delivers_not_raises():
    """Off-loop emit delivers the record instead of dropping it (AR-102 fixed)."""
    import threading

    handler = ConsoleHandler()
    handler.log_queues["custom"] = asyncio.Queue()
    await handler.start_logging()

    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
    thread = threading.Thread(target=lambda: handler.emit(record))
    thread.start()
    thread.join()

    # The bridge moved the record into the custom backend queue.
    got = await asyncio.wait_for(handler.log_queues["custom"].get(), timeout=1)
    assert got.getMessage() == "msg"
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_error_channel_invoked_on_emit_failure(monkeypatch):
    """A bridge put failure surfaces through the error handler after the loop yields."""
    errors = []
    handler = ConsoleHandler(
        error_handler=lambda record, exc: errors.append(exc),
    )
    await handler.start_logging()

    # Fail only the first bridge put; later puts (e.g. shutdown status reporting)
    # no-op so teardown stays clean.
    calls = 0

    def failing_put_once(item):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("put failed")

    monkeypatch.setattr(handler.log_queues["_console"], "put_nowait", failing_put_once)

    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
    handler.emit(record)

    # The bridge moves the record into the console queue on the next loop turn;
    # the monkeypatched put_nowait fails there, so the error surfaces asynchronously.
    await _wait_for(lambda: len(errors) == 1)
    assert isinstance(errors[0], RuntimeError)

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_console_backend_registered_as_peer():
    """The console is registered through register_backend, not special-cased."""
    handler = ConsoleHandler()
    backend = handler._console_backend
    assert backend is not None

    assert "_console" in handler.log_queues
    assert handler.log_queues["_console"] is backend.queue
    assert len(handler.log_worker_factories) == 1
    # The console's drain hook is registered like any other backend's, and its
    # status reporter is registered separately as a post-drain observer.
    assert handler._drain_hooks == [backend.drain]
    assert handler._status_reporters == [backend.report_status]

    await handler.start_logging()
    await handler.stop_logging()


@pytest.mark.asyncio
async def test_stop_logging_drains_all_backends_generically(capsys):
    """stop_logging drains every registered backend through the same generic mechanism."""
    handler = _NoopBrokerHandler(queue_name="broker")
    console = ConsoleHandler()

    await handler.start_logging()
    await console.start_logging()
    handler.emit(_make_record("hello"))
    console.emit(_make_record("hello"))
    await handler.stop_logging(timeout=5)
    await console.stop_logging()

    captured = capsys.readouterr().out
    # Both backends drained through the same drain-hook mechanism; the console
    # reported its own completed drain as a status record.
    assert handler.log_queues["broker"].empty()
    assert console.log_queues["_console"].empty()
    assert "Console Logger has completed processing its queue." in captured


def test_unknown_kwarg_raises_type_error_on_console_handler():
    """A typo'd kwarg on ConsoleHandler fails loudly."""
    with pytest.raises(TypeError):
        ConsoleHandler(unknown_kwarg=True)


def test_console_handler_rejects_backend_config():
    """ConsoleHandler (pure machinery) no longer accepts a broker-only backend_config."""
    with pytest.raises(TypeError):
        ConsoleHandler(backend_config=object())


def test_console_handler_always_registers_console_backend():
    """ConsoleHandler unconditionally registers the console backend."""
    handler = ConsoleHandler()
    assert handler._console_backend is not None
    assert "_console" in handler.log_queues
    assert len(handler.log_worker_factories) == 1


def test_file_and_broker_handlers_register_no_console_backend(tmp_path):
    """File and broker handlers register only their own backend, never console."""
    file_handler = AsyncFileHandler(str(tmp_path / "x.log"))
    broker_handler = _NoopBrokerHandler(queue_name="broker")

    for handler in (file_handler, broker_handler):
        assert "_console" not in handler.log_queues
        assert not hasattr(handler, "_console_backend")
        assert len(handler.log_worker_factories) == 1
