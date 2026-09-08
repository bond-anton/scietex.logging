"""
Shared queue/drain/status-reporting base for the peer backends.

Provides the bounded queue, the public ``worker`` accessor, the ``drain`` hook,
the post-drain ``report_status`` observer, and the ``_report_error`` error
routing shared by ``ConsoleBackend`` and ``FileBackend``. The ``_worker``
coroutines stay backend-specific: each concrete subclass supplies its own sink
logic and assigns the ``_queue_name`` / ``_backend_name`` class attributes.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from ..async_logging_handler import BackendDrainResult, DrainStatus
from ..config import report_error


def _status_record(result: BackendDrainResult) -> logging.LogRecord:
    """
    Build the synthetic shutdown-status record for a backend drain outcome.

    The record's logger name and message use ``result.display_name`` when the
    drain result carries one (the built-in peer backends do); otherwise they
    fall back to deriving a label from the queue key (``result.name`` with a
    leading underscore stripped). The fallback keeps broker handlers and
    custom backends readable without forcing every drain hook to supply a
    display name (AR-008).

    Args:
        result (BackendDrainResult): The drain outcome to report.

    Returns:
        logging.LogRecord: A record describing how the backend's queue drained.
    """
    display = result.display_name or result.name.lstrip("_")
    label = display.capitalize()
    if result.status is DrainStatus.COMPLETED:
        level = logging.INFO
        message = f"{label} Logger has completed processing its queue."
    elif result.status is DrainStatus.TIMEOUT:
        level = logging.ERROR
        message = f"Timeout while waiting for {label} logger to complete its queue."
    else:
        level = logging.ERROR
        message = f"Error while waiting for {label} Logger: {result.error}"
    return logging.LogRecord(
        name=f"{label}Logger",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=None,
    )


class _QueueBackend:
    """
    Shared queue, drain, and shutdown-status-reporting machinery for peer backends.

    Owns the bounded ``asyncio.Queue`` and the drain/status/error-reporting
    surface every peer backend needs during shutdown. Each concrete subclass
    supplies its own ``_worker`` coroutine (the backend-specific sink logic) and
    assigns the ``_queue_name`` / ``_backend_name`` class attributes so the
    inherited ``drain`` / ``_report_error`` methods can name the backend without
    duplicating themselves.
    """

    _queue_name: str
    _backend_name: str
    # Type-only declaration: the concrete ``_worker`` coroutine lives in each
    # subclass, never on the base (the worker-property test asserts
    # ``worker.__func__ is ConsoleBackend._worker``).
    _worker: Callable[[], Coroutine[Any, Any, None]]

    @property
    def _display_name(self) -> str:
        """User-visible label for shutdown-status text (AR-008).

        Derived from the backend class name minus the "Backend" suffix
        (e.g. "ConsoleBackend" -> "Console"), so status records read
        naturally without coupling to the queue-key encoding.
        """
        return self._backend_name.removesuffix("Backend")

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        *,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """
        Initialize the shared queue/drain/status-reporting machinery.

        Args:
            formatter_provider (Callable[[], logging.Formatter | None]): Zero-arg
                callable returning the formatter used to render records. Read at
                work time so ``setFormatter`` on the handler is reflected without a
                manual re-sync.
            running_event (asyncio.Event): Shared event signalling that logging is active.
            maxsize (int): Maximum number of records the queue can hold. Records
                enqueued past this bound are dropped by ``emit``. Defaults to 10000.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a record cannot be written. Defaults to
                None, in which case errors are reported via the ``scietex.logging``
                module logger.
        """
        self.queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(maxsize=maxsize)
        self.formatter_provider = formatter_provider
        self.running_event = running_event
        self.error_handler = error_handler

    @property
    def worker(self) -> Callable[[], Coroutine[Any, Any, None]]:
        """Public worker-factory accessor for registration (AR-115).

        Returns the bound ``_worker`` coroutine method so the owning handler
        and custom integrators can register this backend's worker without
        reaching into a private attribute.
        """
        return self._worker

    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a backend delivery error through the configured error channel.

        Delegates to the shared ``config.report_error`` helper (AR-108).
        """
        report_error(self._backend_name, self.error_handler, record, exc)

    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the backend queue and return the outcome.

        Waits for every queued record to be acknowledged by the backend worker,
        then returns a result describing how the drain concluded so the coordinator
        can surface it to the registered status reporters.

        Args:
            timeout (float): Timeout for draining the backend queue.

        Returns:
            BackendDrainResult: How the backend queue drained.
        """
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult(
                self._queue_name, DrainStatus.TIMEOUT, display_name=self._display_name
            )
        except Exception as exc:
            return BackendDrainResult(
                self._queue_name, DrainStatus.ERROR, exc, display_name=self._display_name
            )
        else:
            return BackendDrainResult(
                self._queue_name, DrainStatus.COMPLETED, display_name=self._display_name
            )

    async def report_status(self, results: list[BackendDrainResult]) -> None:
        """
        Enqueue a synthetic status record for each backend's drain outcome.

        Called by the coordinator after all backends have drained, so the backend
        surfaces how every backend fared during shutdown. Status records are
        best-effort: when the backend queue is full they are dropped rather than
        blocking shutdown on a bounded queue the worker may already be draining.

        Args:
            results (list[BackendDrainResult]): Drain outcomes from every backend.

        Returns:
            None
        """
        for result in results:
            try:
                self.queue.put_nowait(_status_record(result))
            except asyncio.QueueFull:
                # Status records are best-effort shutdown diagnostics. When the
                # backend queue is full, drop them rather than block shutdown on a
                # bounded queue that the worker may already be draining.
                pass
