"""Tests for AsyncBaseHandler class."""

import asyncio
import logging

import pytest

from scietex.logging import AsyncBaseHandler


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


def test_cleanup_threshold_default():
    """Test that cleanup threshold defaults to 100."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    assert handler._queue_put_cleanup_threshold == 100


def test_cleanup_threshold_zero():
    """Test that cleanup threshold defaults to 1 when 0 is provided."""
    handler = AsyncBaseHandler(
        service_name="TestService", worker_id=1, queue_put_cleanup_threshold=0
    )
    assert handler._queue_put_cleanup_threshold == 1


def test_cleanup_threshold_negative():
    """Test that cleanup threshold defaults to 1 for negative values."""
    handler = AsyncBaseHandler(
        service_name="TestService", worker_id=1, queue_put_cleanup_threshold=-5
    )
    assert handler._queue_put_cleanup_threshold == 1


def test_cleanup_threshold_custom():
    """Test that custom cleanup threshold is respected."""
    handler = AsyncBaseHandler(
        service_name="TestService", worker_id=1, queue_put_cleanup_threshold=50
    )
    assert handler._queue_put_cleanup_threshold == 50


@pytest.mark.asyncio
async def test_emit_high_volume_cleanup():
    """Test that periodic cleanup is triggered when threshold is reached."""
    handler = AsyncBaseHandler(
        service_name="TestService", 
        worker_id=1,
        queue_put_cleanup_threshold=5
    )
    await handler.start_logging()
    
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    
    # Track cleanup calls
    cleanup_count = 0
    original_cleanup = handler._cleanup_queue_put_tasks
    
    def tracked_cleanup():
        nonlocal cleanup_count
        cleanup_count += 1
        original_cleanup()
    
    handler._cleanup_queue_put_tasks = tracked_cleanup
    
    # Emit more logs than threshold
    for i in range(10):
        logger.info("Test log message %d", i)
    
    # Verify that cleanup was triggered (at least once, likely multiple times)
    assert cleanup_count >= 1, "Cleanup should have been triggered when threshold was reached"
    
    # After cleanup is triggered, the list should be cleaned up (remove completed tasks)
    # Wait for worker to process tasks
    await asyncio.sleep(0.2)
    handler._cleanup_queue_put_tasks()
    
    # Cleanup should have removed some completed tasks
    assert len(handler.log_queue_put_tasks) < 10
    
    await handler.stop_logging()
