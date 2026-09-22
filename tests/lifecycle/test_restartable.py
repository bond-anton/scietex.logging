"""Restartability tests for the AsyncLoggingHandler start/stop lifecycle."""

import pytest
from conftest import FakeBrokerHandler, FlakyBrokerHandler, _make_record, _wait_for

from scietex.logging import ConsoleHandler


@pytest.mark.asyncio
async def test_mixed_handler_start_stop_start_cycle(capsys):
    """Console and broker both deliver records across two full cycles."""
    broker = FakeBrokerHandler(queue_name="broker")
    console = ConsoleHandler()

    await broker.start_logging()
    await console.start_logging()
    broker.emit(_make_record("mixed-first"))
    console.emit(_make_record("mixed-first"))
    await _wait_for(lambda: len(broker.sent) == 1)
    await broker.stop_logging(timeout=1)
    await console.stop_logging()
    assert broker.client is None

    await broker.start_logging()
    await console.start_logging()
    broker.emit(_make_record("mixed-second"))
    console.emit(_make_record("mixed-second"))
    await _wait_for(lambda: len(broker.sent) == 2)
    await broker.stop_logging(timeout=1)
    await console.stop_logging()
    assert broker.client is None
    assert broker.connect_attempts == 2

    captured = capsys.readouterr().out
    assert "mixed-first" in captured
    assert "mixed-second" in captured
    assert broker.sent[0]["message"] == "mixed-first"
    assert broker.sent[1]["message"] == "mixed-second"


@pytest.mark.asyncio
async def test_double_start_raises():
    """start_logging is not re-entrant while running."""
    handler = ConsoleHandler()

    await handler.start_logging()
    with pytest.raises(RuntimeError):
        await handler.start_logging()
    await handler.stop_logging()


@pytest.mark.asyncio
async def test_close_refuses_later_start():
    """A closed handler refuses start_logging (AR-103)."""
    handler = ConsoleHandler()
    handler.close()

    with pytest.raises(RuntimeError):
        await handler.start_logging()


@pytest.mark.asyncio
async def test_close_while_running_then_stop_is_safe():
    """close() while running leaves workers up until stop_logging, which is not blocked."""
    handler = ConsoleHandler()
    await handler.start_logging()

    handler.close()
    assert not handler.logging_accept_event.is_set()

    await handler.stop_logging()
    assert not handler.logging_running_event.is_set()
    assert handler.log_workers_tasks == []


@pytest.mark.asyncio
async def test_stop_without_start_is_noop():
    """stop_logging on a fresh handler is a no-op that leaves events unset."""
    handler = ConsoleHandler()

    await handler.stop_logging()

    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()
    assert handler.log_workers_tasks == []


@pytest.mark.asyncio
async def test_emit_during_gap_is_dropped():
    """Records emitted between stop and the next start reach no backend."""
    handler = FakeBrokerHandler(
        queue_name="broker",
    )

    await handler.start_logging()
    await handler.stop_logging(timeout=0.5)

    handler.emit(_make_record("dropped"))
    assert not handler.logging_accept_event.is_set()
    assert handler.log_queues["broker"].empty()
    assert handler.sent == []

    await handler.start_logging()
    handler.emit(_make_record("kept"))
    await _wait_for(lambda: len(handler.sent) == 1)
    assert handler.sent[0]["message"] == "kept"
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_send_failure_acks_and_restart_recovers(capsys):
    """A send failure is acked (no false drain timeout); a restart recovers."""
    errors = []
    handler = FlakyBrokerHandler(
        queue_name="broker",
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.failures_before_success = 2  # first two sends fail, then succeed

    # Broker handlers no longer register a console status reporter (Phase 1), so
    # a sibling ConsoleHandler supplies the shutdown status output.
    console = ConsoleHandler()

    await handler.start_logging()
    await console.start_logging()
    handler.emit(_make_record("one"))
    handler.emit(_make_record("two"))
    handler.emit(_make_record("three"))
    await _wait_for(lambda: len(handler.sent) == 1)
    assert handler.sent[0]["message"] == "three"
    assert len(errors) == 2

    # The failed sends were acked, so the drain reports COMPLETED, not a false
    # TIMEOUT, and stop_logging returns promptly.
    await handler.stop_logging(timeout=0.5)
    assert handler.client is None
    await console.stop_logging()
    captured = capsys.readouterr().out
    assert "Console Logger has completed processing its queue." in captured
    assert "Timeout while waiting for" not in captured

    # A fresh start schedules a fresh worker that reconnects and delivers.
    sent_before = len(handler.sent)
    await handler.start_logging()
    handler.emit(_make_record("recovered"))
    await _wait_for(lambda: len(handler.sent) == sent_before + 1)
    assert handler.sent[-1]["message"] == "recovered"
    # connect() runs once for the initial start, once after each of the two send
    # failures (AR-015 resets the client so the next iteration reconnects), and
    # once more for the restart.
    assert handler.connect_attempts == 4
    await handler.stop_logging(timeout=0.5)
