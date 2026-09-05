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
    records asynchronously: per-backend queues, worker coroutines, and the
    accept/running events that gate `emit()` and `stop_logging()`. It does not
    register any backend itself; subclasses add their own queue and worker.

    Attributes:
        log_queues (dict[str, asyncio.Queue]): A dictionary of asyncio.Queue objects
            for each logging backend.
        logging_accept_event (asyncio.Event): Event to signal when the handler can
            accept new logs.
        logging_running_event (asyncio.Event): Event to signal when logging is active.
        log_workers (list[Coroutine]): List of worker coroutine functions for processing
            log messages.
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
            Registers a backend's queue, worker coroutine, and optional drain hook.

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
        **kwargs,
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
            **kwargs: Additional keyword arguments accepted for subclass
                compatibility; they are ignored by the base machinery.
        """
        super().__init__()
        self.error_handler = error_handler
        if worker_id is None:
            worker_id = 1
        if service_name is None:
            service_name = "Service"
        self.formatter = ScietexFormatter(service_name=service_name, worker_id=worker_id)
        self.logging_accept_event = asyncio.Event()  # Indicates if logging accepting events
        self.logging_running_event = asyncio.Event()  # Indicates if logging is running

        self.log_queues: dict[str, asyncio.Queue[logging.LogRecord]] = {}
        self.log_workers: list[Coroutine[Any, Any, None]] = []
        self._drain_hooks: list[DrainHook] = []

        self.log_workers_tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def register_backend(
        self,
        name: str,
        queue: asyncio.Queue[logging.LogRecord],
        worker: Coroutine[Any, Any, None],
        drain: DrainHook | None = None,
    ) -> None:
        """
        Register a backend's queue, worker coroutine, and optional drain hook.

        A backend is registered by its queue name so `emit` fans records into it,
        by its worker coroutine so `start_logging` schedules it, and optionally by a
        `drain` hook that `stop_logging` calls to let the backend control its own
        shutdown. Backends registered first are drained last, so a status-reporting
        backend (e.g. the console) can observe every other backend's drain outcome
        before draining itself.

        Args:
            name (str): Unique name for the backend's queue.
            queue (asyncio.Queue): Queue holding records for this backend.
            worker (Coroutine): Coroutine that processes records from the queue.
            drain (DrainHook, optional): Async callable ``(timeout, results)``
                invoked during `stop_logging` to drain this backend.
        """
        self.log_queues[name] = queue
        self.log_workers.append(worker)
        if drain is not None:
            self._drain_hooks.append(drain)

    async def start_logging(self) -> None:
        """
        Start all logging workers asynchronously.

        Sets the `logging_accept_event` to allow the `emit` method to accept logs.
        Sets the `logging_running_event` to signal that logging has started and creates
        tasks for each worker in `self.log_workers`, allowing them to run concurrently.

        Returns:
            None
        """
        self._loop = asyncio.get_running_loop()
        self.logging_accept_event.set()  # Set the event to indicate logs are accepted
        self.logging_running_event.set()  # Set the event to indicate logging is active
        self.log_workers_tasks = [asyncio.create_task(worker) for worker in self.log_workers]

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
            except Exception as exc:
                self._report_error(record, exc)

    async def stop_logging(self, timeout: float = 5.0) -> None:
        """
        Stop logging and ensure all queues are processed.

        Stops accepting new log records, drains every registered backend through
        its drain hook while the workers are still running, then signals the
        workers to stop and gathers their tasks before closing the handler.
        Backends are drained in reverse registration order so a status-reporting
        backend registered first can observe every other backend's drain outcome
        before draining itself.

        Args:
            timeout (float): Timeout for each backend drain, defaults to 5s.

        Returns:
            None
        """
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

        # Wait for all worker tasks to complete
        if self.log_workers_tasks:
            await asyncio.gather(*self.log_workers_tasks)
        self.close()

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
