"""Tests for AsyncBaseHandler class."""

import logging
import asyncio
import pytest
from scietex.logging import AsyncBaseHandler


@pytest.mark.asyncio
async def test_basic_handler_initialization():
    """Test the initialization of AsyncBaseHandler with default values."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()
    assert handler.stdout_enable is True
    assert (
        "console" in handler.log_queues
    )  # Console queue should be initialized by default
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
    handler.stdout_enable = True  # Ensure stdout is enabled

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
async def test_stop_logging_waits_for_pending_tasks():
    """Test that stop_logging waits for pending tasks to complete."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()

    # Emit several log records
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    for i in range(5):
        logger.info("Test log message %d", i)

    # Stop logging and ensure it waits for all tasks to complete
    await handler.stop_logging()

    # Check that no tasks are left in the queue
    assert handler.log_queue_put_tasks == []
