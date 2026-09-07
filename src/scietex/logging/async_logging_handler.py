"""
Pure machinery base for asynchronous, non-blocking logging in Python.

Provides `AsyncLoggingHandler`, which owns the queue/worker infrastructure and
control events shared by every backend. It has no backend of its own; concrete
handlers (e.g. `AsyncBaseHandler` for console, `AsyncBrokerHandler` for brokers)
register their own queues and workers on top of it.
"""

import asyncio
import logging
import queue
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import (
    LoggingConfig,
    MqttConfig,
    RedisConfig,
    ValkeyConfig,
    report_error,
    validate_queue_maxsize,
)
from .formatter import ScietexFormatter


class DrainStatus(Enum):
    """How a backend queue's drain concluded during shutdown."""

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class BackendDrainResult:
    """Outcome of draining one backend queue during shutdown.

    Attributes:
        name (str): The backend's queue name (e.g. "redis", "valkey").
        status (DrainStatus): How the drain concluded.
        error (BaseException | None): The exception, when status is ERROR.
    """

    name: str
    status: DrainStatus
    error: BaseException | None = None


DrainHook = Callable[[float], Awaitable[BackendDrainResult]]

StatusReporter = Callable[[list[BackendDrainResult]], Awaitable[None]]


class AsyncLoggingHandler(logging.Handler):
    """
    Base machinery for asynchronous, non-blocking logging handlers.

    This handler owns the shared state and control flow for processing log
    records asynchronously: per-backend queues, worker factories, and the
    accept/running events that gate `emit()` and `stop_logging()`. It does not
    register any backend itself; subclasses add their own queue and worker.

    The handler is restartable: `start_logging()` and `stop_logging()` may be
    called repeatedly on the same event loop. Workers are stored as *factories*
    (zero-argument callables returning a fresh coroutine) so each start cycle
    schedules fresh tasks. Any record still queued when `stop_logging` finishes
    is dropped rather than replayed on the next cycle, so each start begins from
    an actually-empty queue.

    Each backend queue is bounded by `queue_maxsize` (default 10000), and a
    thread-safe stdlib `queue.Queue` ingress of the same bound fronts them all.
    Under sustained overload, `emit` drops records when the ingress is full and
    reports the drop through the error channel rather than buffering unboundedly
    or blocking the calling thread.

    Attributes:
        config (LoggingConfig): The single runtime source of truth for handler
            options; read at work time by `emit`/`stop_logging` and the backends.
        log_queues (dict[str, asyncio.Queue]): A dictionary of asyncio.Queue objects
            for each logging backend.
        queue_maxsize (int): Read-only alias for `config.queue_maxsize`; maximum
            number of records each backend queue can hold.
        logging_accept_event (asyncio.Event): Event to signal when the handler can
            accept new logs.
        logging_running_event (asyncio.Event): Event to signal when logging is active.
        log_worker_factories (list[Callable[[], Coroutine]]): List of zero-argument
            worker factories; each returns a fresh worker coroutine when called.
        log_workers_tasks (list[asyncio.Task]): List of asyncio tasks for each worker,
            created in `start_logging`.
        error_handler (callable | None): Read-only alias for `config.error_handler`;
            optional callback invoked with ``(record, exc)`` when a log record cannot
            be delivered.
        formatter (logging.Formatter): Formatter used to render records; the
            default ``ScietexFormatter`` unless a custom one was injected.
        _loop (asyncio.AbstractEventLoop | None): Event loop captured at
            `start_logging` and used only by the bridge's ``call_soon_threadsafe``
            wakeup; `emit()` itself is thread-safe and never uses it directly.
        _ingress (queue.Queue[logging.LogRecord] | None): Thread-safe stdlib queue
            fronting every backend queue; `emit()` writes here from any thread.
            Created in `start_logging`, released in `stop_logging`.
        _ingress_event (asyncio.Event | None): Wakeup event the bridge waits on;
            set via ``loop.call_soon_threadsafe`` whenever `emit()` enqueues.
        _bridge_task (asyncio.Task | None): The bridge task that moves records
            from `_ingress` into the per-backend queues; tracked separately from
            `log_workers_tasks` so worker teardown never reaps it early.
        _drain_hooks (list[DrainHook]): Backend drain hooks in registration order,
            each invoked by `stop_logging` and returning its own `BackendDrainResult`.
        _status_reporters (list[StatusReporter]): Post-drain observers invoked by
            `stop_logging` with the collected drain results (e.g. the console).

    Methods:
        register_backend(name, queue, worker, drain):
            Registers a backend's queue, worker factory, and optional drain hook.

        register_status_reporter(reporter):
            Registers a post-drain observer invoked with all backend drain results.

        start_logging():
            Starts all worker tasks to process log records asynchronously.

        emit(record):
            Queues a log record for each backend if logging is active (thread-safe).

        stop_logging():
            Stops logging by clearing the events and draining every backend.
    """

    def __init__(
        self,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        stdout_enable: bool = True,
        backend_config: RedisConfig | ValkeyConfig | MqttConfig | None = None,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous logging handler machinery.

        Assembles the single ``LoggingConfig`` that governs this handler at work
        time. ``stdout_enable`` and ``backend_config`` are forwarded by the
        concrete subclasses (console and broker handlers respectively); the base
        stores them on ``config`` without acting on them.

        Args:
            service_name (str, optional): Name of the service for log identification.
                Defaults to "Service".
            worker_id (int, optional): Identifier for the worker instance. Defaults to 1.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a log record cannot be delivered. Defaults to
                None, in which case errors are reported via the ``scietex.logging``
                module logger.
            queue_maxsize (int): Maximum number of records each backend queue can
                hold. When a queue is full, `emit` drops the record and reports it
                through the error channel instead of blocking. Defaults to 10000.
                Must be a positive int; invalid values raise ``ValueError``.
            stdout_enable (bool): Whether the console backend is registered by
                `AsyncBaseHandler`. Defaults to True.
            backend_config (RedisConfig | ValkeyConfig | MqttConfig | None): Backend-specific
                config attached by broker subclasses. Defaults to None.
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed from ``service_name`` and ``worker_id``.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        super().__init__()
        if worker_id is None:
            worker_id = 1
        if service_name is None:
            service_name = "Service"
        self.config = LoggingConfig(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            queue_maxsize=validate_queue_maxsize(queue_maxsize),
            stdout_enable=stdout_enable,
            backend_config=backend_config,
        )
        self.formatter = (
            formatter
            if formatter is not None
            else ScietexFormatter(
                service_name=self.config.service_name, worker_id=self.config.worker_id
            )
        )
        self.logging_accept_event = asyncio.Event()  # Indicates if logging accepting events
        self.logging_running_event = asyncio.Event()  # Indicates if logging is running

        self.log_queues: dict[str, asyncio.Queue[logging.LogRecord]] = {}
        self.log_worker_factories: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._drain_hooks: list[DrainHook] = []
        self._status_reporters: list[StatusReporter] = []

        self.log_workers_tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ingress: queue.Queue[logging.LogRecord] | None = None
        self._ingress_event: asyncio.Event | None = None
        self._bridge_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def error_handler(
        self,
    ) -> Callable[[logging.LogRecord | None, Exception], None] | None:
        """Read-only alias for ``config.error_handler``."""
        return self.config.error_handler

    @property
    def queue_maxsize(self) -> int:
        """Read-only alias for ``config.queue_maxsize``."""
        return self.config.queue_maxsize

    @property
    def worker_name(self) -> str:
        """Read-only handler identity ``service_name:worker_id`` derived from config.

        The single owner of handler identity is ``config``; the default
        ``ScietexFormatter`` is built from the same fields and the broker worker
        reads this property, so console and broker output cannot diverge (AR-107).
        A user-injected formatter keeps its own ``worker_name``.
        """
        return f"{self.config.service_name}:{self.config.worker_id}"

    def register_backend(
        self,
        name: str,
        queue: asyncio.Queue[logging.LogRecord],
        worker: Callable[[], Coroutine[Any, Any, None]],
        drain: DrainHook | None = None,
    ) -> None:
        """
        Register a backend's queue, worker factory, and drain hook.

        A backend is registered by its queue name so `emit` fans records into it,
        by its worker factory so `start_logging` schedules a fresh worker task each
        cycle, and by a `drain` hook that `stop_logging` calls to let the backend
        drain its own queue. When `drain` is omitted a generic `queue.join()` drain
        is registered instead, so a drain-less backend is still flushed and
        status-reported at stop rather than silently dropped (AR-110). Each drain
        hook returns its own `BackendDrainResult`; `stop_logging` collects them all
        and hands the results to the registered status reporters.

        Args:
            name (str): Unique name for the backend's queue.
            queue (asyncio.Queue): Queue holding records for this backend.
            worker (Callable[[], Coroutine]): Zero-argument callable returning a
                fresh coroutine that processes records from the queue.
            drain (DrainHook, optional): Async callable ``(timeout) ->
                BackendDrainResult`` invoked during `stop_logging` to drain this
                backend. Defaults to a generic ``queue.join()`` drain of this
                backend's own queue.

        Raises:
            ValueError: If ``name`` is already registered. A duplicate name would
                silently overwrite the queue while doubling the worker and drain
                hook, desynchronizing ``log_queues`` from the parallel lists.
        """
        if name in self.log_queues:
            raise ValueError(f"backend name {name!r} is already registered")
        self.log_queues[name] = queue
        self.log_worker_factories.append(worker)
        if drain is None:
            # A backend registered without a drain hook must not be silently
            # dropped at stop (AR-110). Default to a generic queue.join() drain —
            # the same behavior every built-in backend's drain implements — so a
            # drain-less custom backend still flushes its queue and reports a
            # status result instead of losing records at every shutdown.
            async def default_drain(timeout: float) -> BackendDrainResult:
                try:
                    await asyncio.wait_for(queue.join(), timeout=timeout)
                except asyncio.TimeoutError:
                    return BackendDrainResult(name, DrainStatus.TIMEOUT)
                except Exception as exc:
                    return BackendDrainResult(name, DrainStatus.ERROR, exc)
                else:
                    return BackendDrainResult(name, DrainStatus.COMPLETED)

            drain = default_drain
        self._drain_hooks.append(drain)

    def register_status_reporter(self, reporter: StatusReporter) -> None:
        """
        Register a post-drain observer invoked with every backend's drain result.

        `stop_logging` drains each backend (collecting its `BackendDrainResult`) and
        then calls each registered reporter with the full results list, so a
        status-reporting backend (e.g. the console) can surface how every backend
        fared without relying on drain registration order.

        Args:
            reporter (StatusReporter): Async callable ``(results)`` invoked after all
                backends have drained.
        """
        self._status_reporters.append(reporter)

    async def start_logging(self) -> None:
        """
        Start all logging workers asynchronously.

        Sets the `logging_accept_event` to allow the `emit` method to accept logs.
        Sets the `logging_running_event` to signal that logging has started and creates
        tasks by invoking each worker factory in `self.log_worker_factories`, allowing
        them to run concurrently.

        This method is not re-entrant while running: calling it again before
        `stop_logging` raises `RuntimeError`. A handler that has been stopped may be
        started again on the same event loop; each start schedules fresh worker tasks.

        Returns:
            None
        """
        if self._closed:
            raise RuntimeError(
                "AsyncLoggingHandler.start_logging() called after close(); "
                "a closed handler cannot be restarted"
            )
        if self.logging_running_event.is_set():
            raise RuntimeError("AsyncLoggingHandler.start_logging() called while already running")
        self._loop = asyncio.get_running_loop()
        # Create the thread-safe ingress, its wakeup event, and the bridge task
        # BEFORE raising the accept event so emit() never observes a raised
        # accept event against a not-yet-created ingress. The bridge is tracked
        # separately from log_workers_tasks so the worker gather/cancel logic in
        # stop_logging never reaps it early.
        self._ingress = queue.Queue(maxsize=self.config.queue_maxsize)
        self._ingress_event = asyncio.Event()
        self._bridge_task = asyncio.create_task(self._bridge_loop())
        self.logging_accept_event.set()  # Set the event to indicate logs are accepted
        self.logging_running_event.set()  # Set the event to indicate logging is active
        self.log_workers_tasks = [
            asyncio.create_task(factory()) for factory in self.log_worker_factories
        ]

    def emit(self, record: logging.LogRecord) -> None:
        """
        Queue a log record for each backend when logging is active.

        Called by the logger to handle each log record. Safe to call from any
        thread, including a thread with no running asyncio loop: the record is
        written to a thread-safe stdlib ``queue.Queue`` ingress and a bridge
        task on the event-loop thread re-dispatches it into the per-backend
        ``asyncio.Queue``s. If the logging accept event is not set, the record
        is dropped silently (never raises).

        Args:
            record (logging.LogRecord): The log record to be processed.

        Returns:
            None
        """
        if not self.logging_accept_event.is_set():
            return

        # The ingress is a thread-safe stdlib queue, so put_nowait is safe from
        # any thread (including one with no running loop). It is created in
        # start_logging and released in stop_logging; an emit that races the
        # teardown window (passed the accept check but the ingress already None)
        # drops the record silently — it arrived during shutdown. The loop and
        # ingress event are created and released alongside the ingress, so a
        # None check on all three covers the whole teardown window.
        ingress = self._ingress
        loop = self._loop
        ingress_event = self._ingress_event
        if ingress is None or loop is None or ingress_event is None:
            return
        try:
            ingress.put_nowait(record)
        except queue.Full as exc:
            # Overflow policy: when the ingress is full the record is dropped
            # and reported via the error channel. emit never blocks or buffers
            # unboundedly, so the producer stays non-blocking under overload.
            self._report_error(record, exc)
            return
        except Exception as exc:
            self._report_error(record, exc)
            return

        # Wake the bridge. put_nowait above runs BEFORE this set, so the bridge
        # can never observe the event set before the record is in the queue (no
        # lost wakeup). call_soon_threadsafe is safe from any thread. If the
        # loop is already closed (host forgot stop_logging before asyncio.run
        # returned), report and drop rather than raising into the caller.
        try:
            loop.call_soon_threadsafe(ingress_event.set)
        except Exception as exc:
            self._report_error(record, exc)

    async def _bridge_loop(self) -> None:
        """
        Move records from the thread-safe ingress into the per-backend queues.

        Waits on the ingress event, then drains the ingress with ``get_nowait``
        and re-dispatches each record into every backend ``asyncio.Queue`` via
        ``put_nowait``. The bridge only MOVES records — it never formats or
        mutates them; all formatting stays on the loop thread in each worker.
        A backend queue that is full (or any other put failure) is reported via
        the error channel and the record is dropped for that backend.

        Returns:
            None
        """
        # start_logging sets the ingress and its event before creating this task
        # and stop_logging cancels the task before clearing them, so both are
        # non-None for the task's lifetime. Local references keep them narrowed
        # across the awaits below (an instance attribute could be mutated by
        # another coroutine between awaits, defeating type narrowing).
        assert self._ingress is not None and self._ingress_event is not None
        ingress = self._ingress
        ingress_event = self._ingress_event
        while True:
            await ingress_event.wait()
            ingress_event.clear()
            while True:
                try:
                    record = ingress.get_nowait()
                except queue.Empty:
                    break
                for backend_queue in self.log_queues.values():
                    try:
                        backend_queue.put_nowait(record)
                    except asyncio.QueueFull as exc:
                        self._report_error(record, exc)
                    except Exception as exc:
                        self._report_error(record, exc)

    async def stop_logging(self, timeout: float = 5.0) -> None:
        """
        Stop logging and ensure all queues are processed.

        Stops accepting new log records, drains every registered backend through
        its drain hook while the workers are still running, then signals the
        workers to stop and gathers their tasks. The coordinator owns result
        collection: each drain hook returns its own `BackendDrainResult`, and the
        collected results are handed to the registered status reporters (e.g. the
        console) as an explicit post-drain step.

        Any record still queued after the drain window and worker teardown is
        dropped, not replayed on the next `start_logging` cycle. A worker stuck
        on an unreachable broker (or a queue whose drain timed out) can leave
        records behind; clearing them keeps the restart contract honest.

        This method is idempotent: calling it when logging is not running (never
        started, or already stopped) is a no-op. After it returns, the handler may
        be started again via `start_logging`.

        Args:
            timeout (float): Shared timeout for the concurrent backend drains and
                the worker gather, defaults to 5s.

        Returns:
            None
        """
        if not self.logging_running_event.is_set() and not self.log_workers_tasks:
            return

        # Stop accepting new log records
        self.logging_accept_event.clear()

        # Stop the bridge and flush the ingress into the backend queues BEFORE
        # the backend drains run. The bridge is cancelled first so it stops
        # consuming; then the same get_nowait -> put_nowait fan-out runs inline
        # here until the ingress is empty. This guarantees no record still in
        # the ingress is lost when the backend drains complete and the workers
        # stop. The bridge is transparent machinery: it produces no
        # BackendDrainResult of its own.
        if self._bridge_task is not None:
            self._bridge_task.cancel()
            try:
                await self._bridge_task
            except asyncio.CancelledError:
                pass
        if self._ingress is not None:
            while True:
                try:
                    record = self._ingress.get_nowait()
                except queue.Empty:
                    break
                for backend_queue in self.log_queues.values():
                    try:
                        backend_queue.put_nowait(record)
                    except asyncio.QueueFull as exc:
                        self._report_error(record, exc)
                    except Exception as exc:
                        self._report_error(record, exc)

        # Drain every backend concurrently under one shared timeout (AR-105).
        # gather schedules all drain hooks at once and preserves registration
        # order in the returned list, so the status reporters still receive
        # results in the order the backends were registered — no status-reporter
        # semantics change. This removes the reverse-registration-order coupling
        # between a status reporter and the backends it reports on, and keeps a
        # slow backend from serializing the whole shutdown.
        results: list[BackendDrainResult] = list(
            await asyncio.gather(*(drain(timeout) for drain in self._drain_hooks))
        )
        for reporter in self._status_reporters:
            await reporter(results)

        # Signal workers to stop processing now that every drain has concluded.
        self.logging_running_event.clear()

        # Wait for all worker tasks to complete, then forget them so a later stop
        # does not re-gather already-finished tasks. A worker blocked inside
        # connect()/send_message() cannot observe the running-event clear until the
        # call returns, so bound the wait with the same timeout used for the drains
        # and cancel any stragglers rather than hanging graceful shutdown on an
        # unreachable broker.
        if self.log_workers_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self.log_workers_tasks), timeout)
            except asyncio.TimeoutError:
                # Reap any worker still stuck in an await that cannot observe the
                # running-event clear. cancel() is a no-op on an already-cancelled
                # task, and gather(return_exceptions=True) lets each worker run its
                # finally cleanup (e.g. acking an in-flight record) to completion.
                for task in self.log_workers_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*self.log_workers_tasks, return_exceptions=True)
        self.log_workers_tasks = []

        # Drop any records still undelivered after the drain window and worker
        # teardown (AR-020). A stalled worker or unreachable broker can leave
        # records queued; without this they would replay on the next start, so the
        # restart would not begin from an actually-empty queue. task_done() pairs
        # with get_nowait() to keep the unfinished-task count balanced.
        for backend_queue in self.log_queues.values():
            while not backend_queue.empty():
                backend_queue.get_nowait()
                backend_queue.task_done()
        # Clear any leftover ingress entries (an in-flight emit that raced the
        # teardown) so the next start begins from an actually-empty ingress.
        if self._ingress is not None:
            while not self._ingress.empty():
                self._ingress.get_nowait()

        # Release the captured loop and ingress references now that teardown is
        # complete (AR-113). They are kept until this point so a record still in
        # flight during the drain window can be enqueued; the next start_logging
        # re-captures the running loop and creates a fresh ingress + bridge.
        self._loop = None
        self._ingress = None
        self._ingress_event = None
        self._bridge_task = None

    def close(self) -> None:
        """
        Mark the handler closed and refuse any later start (AR-103).

        Called by the stdlib ``logging.shutdown()`` hook, which runs
        synchronously. Closing cannot await, so it does NOT stop the workers or
        close a broker client — graceful teardown still requires
        ``await stop_logging()``. It clears the accept event so no new records are
        enqueued, but a ``close()`` while running leaves the workers running until
        the host calls ``await stop_logging()`` (which is not blocked by
        ``_closed``).
        """
        super().close()
        self._closed = True
        self.logging_accept_event.clear()

    def flush(self) -> None:
        """
        Documented no-op.

        The stdlib ``shutdown()`` hook calls ``flush()`` before ``close()``.
        Records are flushed by the async workers during ``stop_logging()``, which
        cannot be awaited from this synchronous hook, so there is nothing to do
        here.
        """

    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a delivery error through the configured error channel.

        Delegates to the shared ``config.report_error`` helper (AR-108) so the
        handler and the console backend share one error-routing policy.
        """
        report_error(type(self).__name__, self.config.error_handler, record, exc)
