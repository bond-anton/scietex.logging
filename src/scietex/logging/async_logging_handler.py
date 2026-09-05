"""
Pure machinery base for asynchronous, non-blocking logging in Python.

Provides `AsyncLoggingHandler`, which owns the queue/worker infrastructure and
control events shared by every backend. It has no backend of its own; concrete
handlers (e.g. `AsyncBaseHandler` for console, `AsyncBrokerHandler` for brokers)
register their own queues and workers on top of it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .config import LoggingConfig, validate_queue_maxsize
from .formatter import ScietexFormatter

_error_logger = logging.getLogger("scietex.logging")


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


DrainHook = Callable[[float, list[BackendDrainResult]], Awaitable[None]]


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
    schedules fresh tasks from a clean queue.

    Each backend queue is bounded by `queue_maxsize` (default 10000). Under
    sustained overload, `emit` drops records for any full backend queue and
    reports the drop through the error channel rather than buffering unboundedly
    or blocking the calling thread.

    Attributes:
        log_queues (dict[str, asyncio.Queue]): A dictionary of asyncio.Queue objects
            for each logging backend.
        queue_maxsize (int): Maximum number of records each backend queue can hold.
        logging_accept_event (asyncio.Event): Event to signal when the handler can
            accept new logs.
        logging_running_event (asyncio.Event): Event to signal when logging is active.
        log_worker_factories (list[Callable[[], Coroutine]]): List of zero-argument
            worker factories; each returns a fresh worker coroutine when called.
        log_workers_tasks (list[asyncio.Task]): List of asyncio tasks for each worker,
            created in `start_logging`.
        error_handler (callable | None): Optional callback invoked with
            ``(record, exc)`` when a log record cannot be delivered.
        _loop (asyncio.AbstractEventLoop | None): Event loop captured at
            `start_logging`; `emit()` is only valid on that loop's thread.
        _drain_hooks (list[DrainHook]): Backend drain hooks in registration order,
            invoked (in reverse) by `stop_logging`.

    Methods:
        register_backend(name, queue, worker, drain):
            Registers a backend's queue, worker factory, and optional drain hook.

        start_logging():
            Starts all worker tasks to process log records asynchronously.

        emit(record):
            Queues a log record for each backend if logging is active.

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
    ) -> None:
        """
        Initialize the asynchronous logging handler machinery.

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
        )
        self.error_handler = self.config.error_handler
        self.queue_maxsize = self.config.queue_maxsize
        self.formatter = ScietexFormatter(
            service_name=self.config.service_name, worker_id=self.config.worker_id
        )
        self.logging_accept_event = asyncio.Event()  # Indicates if logging accepting events
        self.logging_running_event = asyncio.Event()  # Indicates if logging is running

        self.log_queues: dict[str, asyncio.Queue[logging.LogRecord]] = {}
        self.log_worker_factories: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._drain_hooks: list[DrainHook] = []

        self.log_workers_tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_backend(
        self,
        name: str,
        queue: asyncio.Queue[logging.LogRecord],
        worker: Callable[[], Coroutine[Any, Any, None]],
        drain: DrainHook | None = None,
    ) -> None:
        """
        Register a backend's queue, worker factory, and optional drain hook.

        A backend is registered by its queue name so `emit` fans records into it,
        by its worker factory so `start_logging` schedules a fresh worker task each
        cycle, and optionally by a `drain` hook that `stop_logging` calls to let the
        backend control its own shutdown. Backends registered first are drained last,
        so a status-reporting backend (e.g. the console) can observe every other
        backend's drain outcome before draining itself.

        Args:
            name (str): Unique name for the backend's queue.
            queue (asyncio.Queue): Queue holding records for this backend.
            worker (Callable[[], Coroutine]): Zero-argument callable returning a
                fresh coroutine that processes records from the queue.
            drain (DrainHook, optional): Async callable ``(timeout, results)``
                invoked during `stop_logging` to drain this backend.
        """
        self.log_queues[name] = queue
        self.log_worker_factories.append(worker)
        if drain is not None:
            self._drain_hooks.append(drain)

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
        if self.logging_running_event.is_set():
            raise RuntimeError("AsyncLoggingHandler.start_logging() called while already running")
        self._loop = asyncio.get_running_loop()
        self.logging_accept_event.set()  # Set the event to indicate logs are accepted
        self.logging_running_event.set()  # Set the event to indicate logging is active
        self.log_workers_tasks = [
            asyncio.create_task(factory()) for factory in self.log_worker_factories
        ]

    def emit(self, record: logging.LogRecord) -> None:
        """
        Queue a log record for each backend when logging is active.

        Called by the logger to handle each log record. If the logging accept event
        is set, queues the record in the queues. Each backend can have
        a unique queue, allowing separate handling in different workers.

        Must be called from the asyncio event-loop thread; off-loop logging raises
        `RuntimeError`.

        Args:
            record (logging.LogRecord): The log record to be processed.

        Returns:
            None
        """
        if not self.logging_accept_event.is_set():
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is not self._loop:
            raise RuntimeError(
                "AsyncLoggingHandler.emit() must be called from the asyncio event-loop "
                "thread; off-loop logging is not supported"
            )

        # Put the record in each queue synchronously; failures are reported, not swallowed.
        for queue in self.log_queues.values():
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull as exc:
                # Overflow policy: when a backend queue is full the record is dropped
                # and reported via the error channel. emit never blocks or buffers
                # unboundedly, so the producer stays non-blocking under overload.
                self._report_error(record, exc)
            except Exception as exc:
                self._report_error(record, exc)

    async def stop_logging(self, timeout: float = 5.0) -> None:
        """
        Stop logging and ensure all queues are processed.

        Stops accepting new log records, drains every registered backend through
        its drain hook while the workers are still running, then signals the
        workers to stop and gathers their tasks. Backends are drained in reverse
        registration order so a status-reporting backend registered first can
        observe every other backend's drain outcome before draining itself.

        This method is idempotent: calling it when logging is not running (never
        started, or already stopped) is a no-op. After it returns, the handler may
        be started again via `start_logging`.

        Args:
            timeout (float): Timeout for each backend drain, defaults to 5s.

        Returns:
            None
        """
        if not self.logging_running_event.is_set() and not self.log_workers_tasks:
            return

        # Stop accepting new log records
        self.logging_accept_event.clear()

        # Drain every backend generically while the workers are still running. Results
        # are collected so a backend that reports shutdown status (e.g. the console)
        # can observe how each other backend fared before it drains itself.
        results: list[BackendDrainResult] = []
        for drain in reversed(self._drain_hooks):
            await drain(timeout, results)

        # Signal workers to stop processing now that every drain has concluded.
        self.logging_running_event.clear()

        # Wait for all worker tasks to complete, then forget them so a later stop
        # does not re-gather already-finished tasks.
        if self.log_workers_tasks:
            await asyncio.gather(*self.log_workers_tasks)
        self.log_workers_tasks = []

    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """
        Report a delivery error through the configured error channel.

        If an `error_handler` callback is configured it is invoked; otherwise the
        error is logged through the `scietex.logging` module logger.

        Args:
            record (logging.LogRecord | None): The record whose delivery failed, or
                None when the failure is not tied to a specific record.
            exc (Exception): The exception that caused the failure.
        """
        if self.error_handler is not None:
            try:
                self.error_handler(record, exc)
            except Exception:
                # The error reporter must never crash the logging path.
                pass
        else:
            _error_logger.error(
                "%s failed to deliver a log record: %s",
                type(self).__name__,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
