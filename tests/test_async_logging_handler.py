"""Tests for the AsyncLoggingHandler pure machinery base."""

import asyncio
import logging
import threading
import time

import pytest

from scietex.logging import AsyncLoggingHandler
from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus


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


class BareHandler(AsyncLoggingHandler):
    """Handler that registers no backend, proving the base owns no sink."""


class DeliveringHandler(AsyncLoggingHandler):
    """BareHandler subclass that registers one real backend with a draining worker."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivered: list[str] = []
        self.queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(maxsize=100)
        self.register_backend("deliver", self.queue, self._worker, self._drain)

    async def _worker(self) -> None:
        while self.logging_running_event.is_set() or not self.queue.empty():
            try:
                record = await asyncio.wait_for(self.queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            self.delivered.append(record.getMessage())
            self.queue.task_done()

    async def _drain(self, timeout: float) -> BackendDrainResult:
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("deliver", DrainStatus.TIMEOUT)
        return BackendDrainResult("deliver", DrainStatus.COMPLETED)


@pytest.mark.asyncio
async def test_emit_from_worker_thread_delivers():
    """Off-loop emit (from a threading.Thread) delivers the record to the backend."""
    handler = DeliveringHandler()
    await handler.start_logging()

    thread = threading.Thread(target=lambda: handler.emit(_make_record("from-thread")))
    thread.start()
    thread.join()

    await _wait_for(lambda: len(handler.delivered) == 1)
    assert handler.delivered == ["from-thread"]
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_emit_from_no_loop_thread_delivers():
    """emit from a thread with no running asyncio loop delivers (no RuntimeError)."""
    handler = DeliveringHandler()
    await handler.start_logging()

    # A plain threading.Thread has no running loop; emit must still deliver.
    thread = threading.Thread(target=lambda: handler.emit(_make_record("no-loop")))
    thread.start()
    thread.join()

    await _wait_for(lambda: len(handler.delivered) == 1)
    assert handler.delivered == ["no-loop"]
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_emit_ordering_preserved_across_threads():
    """Records emitted from multiple threads arrive at the backend in FIFO order."""
    handler = DeliveringHandler()
    await handler.start_logging()

    # Emit N records from N threads, then one from the loop thread. The bridge
    # drains the ingress FIFO, so the per-backend order matches emit order.
    n = 20
    threads = [
        threading.Thread(target=lambda i=i: handler.emit(_make_record(f"t{i}"))) for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    handler.emit(_make_record("loop-last"))

    await _wait_for(lambda: len(handler.delivered) == n + 1)
    assert handler.delivered == [f"t{i}" for i in range(n)] + ["loop-last"]
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_restartable_with_bridge():
    """Two full start/stop cycles deliver both cycles' records via fresh bridges."""
    handler = DeliveringHandler()

    for i in range(2):
        await handler.start_logging()
        handler.emit(_make_record(f"cycle-{i}"))
        await _wait_for(lambda: len(handler.delivered) == i + 1)
        await handler.stop_logging(timeout=0.5)

    assert handler.delivered == ["cycle-0", "cycle-1"]
    assert handler._ingress is None  # released on stop
    assert handler._bridge_task is None  # released on stop


def test_pure_handler_owns_no_backend():
    """The base machinery holds no queue, worker, drain hook, or reporter on its own."""
    handler = BareHandler()

    assert handler.log_queues == {}
    assert handler.log_worker_factories == []
    assert handler._drain_hooks == []
    assert handler._status_reporters == []


@pytest.mark.asyncio
async def test_pure_handler_starts_and_stops_cleanly():
    """A backend-less handler starts and stops without any queue activity."""
    handler = BareHandler()

    await handler.start_logging()
    assert handler.logging_accept_event.is_set()
    assert handler.logging_running_event.is_set()
    assert handler.log_workers_tasks == []

    await handler.stop_logging()
    assert handler.log_queues == {}
    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()


def test_unknown_kwarg_raises_type_error():
    """A typo'd kwarg fails loudly instead of being silently swallowed."""
    with pytest.raises(TypeError):
        BareHandler(unknown_kwarg=True)


def test_base_owns_no_formatter():
    """The pure-machinery base installs no formatter (console/file own theirs)."""
    handler = BareHandler()

    # stdlib logging.Handler.__init__ sets self.formatter = None, but the base
    # installs no ScietexFormatter and owns no formatter concept of its own.
    assert handler.formatter is None


def test_base_rejects_formatter_kwarg():
    """formatter= is console/file-specific; the machinery base rejects it (TypeError)."""
    with pytest.raises(TypeError):
        BareHandler(formatter=logging.Formatter("%(message)s"))


def test_config_exposes_machinery_options():
    handler = BareHandler(queue_maxsize=123, error_handler=lambda r, e: None)
    assert handler.config.queue_maxsize == 123
    assert handler.config.error_handler is not None
    assert handler.queue_maxsize == 123
    assert handler.error_handler is not None


def test_config_is_single_source_of_truth():
    """Runtime state reads self.config; the flat attributes are read-only aliases."""

    def err(record, exc):
        pass

    handler = BareHandler(queue_maxsize=123, error_handler=err)

    # The flat aliases mirror config, which is authoritative.
    assert handler.queue_maxsize == handler.config.queue_maxsize == 123
    assert handler.error_handler is handler.config.error_handler
    assert handler.error_handler is err

    # The aliases are read-only, so they cannot drift from the config that drives behavior.
    with pytest.raises(AttributeError):
        handler.queue_maxsize = 5
    with pytest.raises(AttributeError):
        handler.error_handler = None


@pytest.mark.asyncio
async def test_stop_logging_collects_results_and_reports():
    """stop_logging drains each backend, collects results, then reports them."""
    reported = []

    class ReportingHandler(AsyncLoggingHandler):
        def __init__(self):
            super().__init__()
            self.register_backend("a", asyncio.Queue(), self._noop_worker, self._drain_a)
            self.register_backend("b", asyncio.Queue(), self._noop_worker, self._drain_b)
            self.register_status_reporter(self._report)

        async def _report(self, results):
            reported.append(list(results))

        async def _noop_worker(self):
            while self.logging_running_event.is_set():
                await asyncio.sleep(0.01)

        async def _drain_a(self, timeout):
            return BackendDrainResult("a", DrainStatus.COMPLETED)

        async def _drain_b(self, timeout):
            return BackendDrainResult("b", DrainStatus.TIMEOUT)

    handler = ReportingHandler()
    await handler.start_logging()
    await handler.stop_logging(timeout=0.1)

    assert [r.name for r in reported[0]] == ["a", "b"]  # both backend results collected
    assert reported[0][0].status is DrainStatus.COMPLETED
    assert reported[0][1].status is DrainStatus.TIMEOUT


@pytest.mark.asyncio
async def test_stop_clears_undelivered_records():
    """Undelivered records are dropped on stop, not replayed on the next start (AR-020)."""
    delivered: list[str] = []
    handler = BareHandler()
    queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(maxsize=10)
    stalled = {"on": True}

    async def worker() -> None:
        while handler.logging_running_event.is_set() or not queue.empty():
            if stalled["on"]:
                await asyncio.sleep(1)
                continue
            record = await asyncio.wait_for(queue.get(), 1)
            delivered.append(record.getMessage())
            queue.task_done()

    async def drain(timeout: float) -> BackendDrainResult:
        try:
            await asyncio.wait_for(queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("stalled", DrainStatus.TIMEOUT)
        return BackendDrainResult("stalled", DrainStatus.COMPLETED)

    handler.register_backend("stalled", queue, worker, drain)

    await handler.start_logging()
    handler.emit(_make_record("stale-one"))
    handler.emit(_make_record("stale-two"))
    # The bridge moves records into the backend queue asynchronously; wait until
    # both are queued (the stalled worker never drains them).
    await _wait_for(lambda: queue.qsize() == 2)

    await handler.stop_logging(timeout=0.05)

    # The undelivered records were cleared, not left behind for a later replay.
    assert queue.empty()

    # A subsequent start delivers only fresh records; the stale ones are gone.
    stalled["on"] = False
    await handler.start_logging()
    handler.emit(_make_record("fresh"))
    await _wait_for(lambda: len(delivered) == 1)
    assert delivered == ["fresh"]
    await handler.stop_logging(timeout=0.5)


def test_register_backend_duplicate_name_raises():
    """register_backend rejects a second backend under an already-registered name (AR-028)."""

    async def worker() -> None:
        pass

    handler = BareHandler()
    handler.register_backend("dup", asyncio.Queue(), worker)

    with pytest.raises(ValueError):
        handler.register_backend("dup", asyncio.Queue(), worker)


@pytest.mark.asyncio
async def test_register_backend_without_drain_gets_default_queue_join_drain():
    """A backend registered without a drain hook gets a generic queue.join() drain (AR-110)."""
    handler = BareHandler()
    queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue()
    delivered: list[str] = []

    async def worker() -> None:
        while handler.logging_running_event.is_set() or not queue.empty():
            try:
                record = await asyncio.wait_for(queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            delivered.append(record.getMessage())
            queue.task_done()

    handler.register_backend("nodrain", queue, worker)  # no drain arg

    assert len(handler._drain_hooks) == 1  # a default drain hook was registered

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: len(delivered) == 1)
    await handler.stop_logging(timeout=0.5)

    assert delivered == ["hello"]
    assert queue.empty()


@pytest.mark.asyncio
async def test_report_error_falls_back_to_module_logger_when_error_handler_raises(caplog):
    """A raising error_handler falls back to the module logger, not silence (AR-031)."""

    def bad_error_handler(record, exc):
        raise RuntimeError("error handler is broken")

    handler = BareHandler(error_handler=bad_error_handler, queue_maxsize=1)

    await handler.start_logging()
    handler.emit(_make_record("accepted"))  # fills the ingress (maxsize=1)
    with caplog.at_level(logging.ERROR):
        handler.emit(_make_record("dropped"))  # ingress full -> _report_error -> fallback
    await handler.stop_logging(timeout=0.5)

    assert any(
        record.name == "scietex.logging" and "failed to deliver a log record" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_emit_off_loop_delivers_not_drops():
    """Off-loop emit now delivers the record instead of dropping it (AR-102 fixed)."""
    handler = DeliveringHandler()
    await handler.start_logging()

    # Emit from a thread whose loop is NOT the handler's loop. The old guard
    # dropped this; the thread-safe ingress now delivers it.
    thread = threading.Thread(target=lambda: handler.emit(_make_record("off-loop")))
    thread.start()
    thread.join()

    await _wait_for(lambda: len(handler.delivered) == 1)
    assert handler.delivered == ["off-loop"]
    await handler.stop_logging(timeout=0.5)


@pytest.mark.asyncio
async def test_stop_logging_drains_backends_concurrently():
    """Backend drains run concurrently under one shared timeout (AR-105)."""

    class SlowDrainHandler(AsyncLoggingHandler):
        def __init__(self) -> None:
            super().__init__()
            self.register_backend("a", asyncio.Queue(), self._noop_worker, self._slow_drain)
            self.register_backend("b", asyncio.Queue(), self._noop_worker, self._slow_drain)

        async def _noop_worker(self) -> None:
            while self.logging_running_event.is_set():
                await asyncio.sleep(0.01)

        async def _slow_drain(self, timeout: float) -> BackendDrainResult:
            await asyncio.sleep(0.2)
            return BackendDrainResult("x", DrainStatus.COMPLETED)

    handler = SlowDrainHandler()
    await handler.start_logging()

    start = time.monotonic()
    await handler.stop_logging(timeout=5.0)
    elapsed = time.monotonic() - start

    # Two 0.2s drains serialized would take ~0.4s; concurrent they share the
    # window and finish in ~0.2s.
    assert elapsed < 0.35


def test_close_sets_closed_flag_and_calls_super():
    """close() marks the handler closed and is idempotent (AR-103)."""
    handler = BareHandler()

    handler.close()
    assert handler._closed is True

    handler.close()  # idempotent
    assert handler._closed is True
