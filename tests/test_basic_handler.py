"""Tests for AsyncBaseHandler class."""

import asyncio
import logging

import pytest

from scietex.logging import AsyncBaseHandler, ScietexFormatter
from scietex.logging.message_broker_handler import AsyncBrokerHandler


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


class _NoopBrokerHandler(AsyncBrokerHandler):
    """Minimal broker that connects instantly and acknowledges every record."""

    async def connect(self) -> None:
        self.client = object()

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        pass


@pytest.mark.asyncio
async def test_basic_handler_initialization():
    """Test the initialization of AsyncBaseHandler with default values."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()
    assert handler.stdout_enable is True
    assert "console" in handler.log_queues  # Console queue should be initialized by default
    await handler.stop_logging()


@pytest.mark.asyncio
async def test_start_and_stop_logging():
    """Test starting and stopping the logging process."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
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
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()

    # Create a test log record
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    # Emit a log record
    logger.info("Test log message")

    # Ensure the log record was added to the console queue
    log_record = await asyncio.wait_for(handler.log_queues["console"].get(), timeout=1)
    assert log_record.getMessage() == "Test log message"

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_console_worker_outputs_log(capsys):
    """Test that the console worker processes and outputs logs correctly."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)

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
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    formatter = ScietexFormatter(
        service_name="TestService",
        worker_id=1,
        fmt="%(levelname)s | %(message)s",
    )
    handler.setFormatter(formatter)

    # The console backend must now use the custom formatter, not the stale default.
    assert handler._console_backend is not None
    assert handler._console_backend.formatter is formatter

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
async def test_stop_logging_drains_queues():
    """Test that stop_logging waits for all queued records to be processed."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()

    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    for i in range(5):
        logger.info("Test log message %d", i)

    await handler.stop_logging()

    # Records were queued synchronously and drained by the console worker. The
    # queue.join() inside stop_logging already guarantees every item was acknowledged.
    assert handler.log_queues["console"].empty()


@pytest.mark.asyncio
async def test_emit_puts_records_synchronously():
    """Test that emit puts records directly via put_nowait without scheduling tasks."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enable=False)
    handler.log_queues["custom"] = asyncio.Queue()
    handler._loop = asyncio.get_running_loop()
    handler.logging_accept_event.set()

    record = logging.LogRecord("test", logging.INFO, "", 0, "sync message", None, None)
    handler.emit(record)

    assert handler.log_queues["custom"].qsize() == 1


def test_emit_raises_when_called_off_loop():
    """Test that emit raises RuntimeError when called outside the event-loop thread."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enable=False)
    handler._loop = object()  # Sentinel loop that never matches a running loop
    handler.logging_accept_event.set()

    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
    with pytest.raises(RuntimeError):
        handler.emit(record)


@pytest.mark.asyncio
async def test_error_channel_invoked_on_emit_failure(monkeypatch):
    """Test that emit reports queue-put failures through the error handler."""
    errors = []
    handler = AsyncBaseHandler(
        service_name="TestService",
        worker_id=1,
        error_handler=lambda record, exc: errors.append(exc),
    )
    await handler.start_logging()

    # Fail only the emit put; let the shutdown status reporting enqueue cleanly.
    calls = 0

    def failing_put_once(item):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("put failed")

    monkeypatch.setattr(handler.log_queues["console"], "put_nowait", failing_put_once)

    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
    handler.emit(record)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)

    await handler.stop_logging()


@pytest.mark.asyncio
async def test_console_backend_registered_as_peer():
    """The console is registered through register_backend, not special-cased."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    backend = handler._console_backend
    assert backend is not None

    assert "console" in handler.log_queues
    assert handler.log_queues["console"] is backend.queue
    assert len(handler.log_worker_factories) == 1
    # The console's drain hook is registered like any other backend's, and its
    # status reporter is registered separately as a post-drain observer.
    assert handler._drain_hooks == [backend.drain]
    assert handler._status_reporters == [backend.report_status]

    await handler.start_logging()
    await handler.stop_logging()


def test_console_backend_absent_when_stdout_disabled():
    """stdout_enable=False leaves no console queue (console is a peer, not privileged)."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enable=False)

    assert handler.stdout_enable is False
    assert handler._console_backend is None
    assert "console" not in handler.log_queues
    assert handler.log_queues == {}
    assert handler.log_worker_factories == []
    assert handler._drain_hooks == []
    assert handler._status_reporters == []


@pytest.mark.asyncio
async def test_stop_logging_drains_all_backends_generically(capsys):
    """stop_logging drains console and broker through the same generic mechanism."""
    handler = _NoopBrokerHandler(queue_name="broker", service_name="TestService", worker_id=1)
    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await handler.stop_logging(timeout=5)

    captured = capsys.readouterr().out
    # The broker drain completed and the console reported its outcome as a status record.
    assert "Broker Logger has completed processing its queue." in captured
    assert handler.log_queues["broker"].empty()


def test_unknown_kwarg_raises_type_error_on_base_handler():
    """A typo'd kwarg on AsyncBaseHandler fails loudly."""
    with pytest.raises(TypeError):
        AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enabel=True)


def test_config_exposes_stdout_enable():
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enable=False)
    assert handler.config.stdout_enable is False
    assert handler.stdout_enable is False  # backward-compat alias
