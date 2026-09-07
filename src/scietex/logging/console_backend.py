"""
Console backend for the asynchronous logging framework.

Encapsulates the console sink as a peer backend: it owns its queue, its worker
coroutine, and a provider for the handler's formatter. `AsyncBaseHandler`
registers this backend's queue and worker into the shared machinery the same
way `AsyncBrokerHandler` registers its broker queue and worker.
"""

import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from ._executor import _WriteExecutor
from .async_logging_handler import BackendDrainResult, DrainStatus
from .config import report_error


def _status_record(result: BackendDrainResult) -> logging.LogRecord:
    """
    Build the synthetic shutdown-status record for a backend drain outcome.

    Args:
        result (BackendDrainResult): The drain outcome to report.

    Returns:
        logging.LogRecord: A record describing how the backend's queue drained.
    """
    if result.status is DrainStatus.COMPLETED:
        level = logging.INFO
        message = f"{result.name.capitalize()} Logger has completed processing its queue."
    elif result.status is DrainStatus.TIMEOUT:
        level = logging.ERROR
        message = f"Timeout while waiting for {result.name} logger to complete its queue."
    else:
        level = logging.ERROR
        message = f"Error while waiting for {result.name} Logger: {result.error}"
    return logging.LogRecord(
        name=f"{result.name.capitalize()}Logger",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=None,
    )


class ConsoleBackend:
    """
    Console sink for asynchronous log records.

    Owns an `asyncio.Queue` and a worker coroutine that formats queued records
    and writes them to standard output. It observes the handler's running event
    so it can wind down when logging stops.

    During shutdown the coordinator drains the console queue via `drain` and, after
    every backend has drained, invokes `report_status` to enqueue synthetic status
    records describing how each backend fared. The console is therefore a post-drain
    observer, not a drain hook that reads a shared results list mid-iteration.

    Attributes:
        queue (asyncio.Queue[logging.LogRecord]): Queue holding records destined
            for standard output.
        formatter_provider (Callable[[], logging.Formatter | None]): Zero-arg
            callable returning the handler's current formatter, read at work time
            so the console never holds a stale copy.
        running_event (asyncio.Event): Shared event signalling that logging is active.
        error_handler (Callable | None): Optional callback invoked with
            ``(record, exc)`` when a record cannot be written to standard output.
    """

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """
        Initialize the console backend.

        Args:
            formatter_provider (Callable[[], logging.Formatter | None]): Zero-arg
                callable returning the formatter used to render records. Read at
                work time so `setFormatter` on the handler is reflected without a
                manual re-sync.
            running_event (asyncio.Event): Shared event signalling that logging is active.
            maxsize (int): Maximum number of records the queue can hold. Records
                enqueued past this bound are dropped by `emit`. Defaults to 10000.
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

        Returns the bound ``_worker`` coroutine method so ``AsyncBaseHandler``
        and custom integrators can register this backend's worker without
        reaching into a private attribute.
        """
        return self._worker

    async def _worker(self) -> None:
        """
        Drain the console queue, writing formatted records to standard output.

        Continues as long as logging is active or records remain queued. The
        short timeout on `queue.get` lets the worker observe the running event
        being cleared without blocking forever.

        Each record is formatted on the event-loop thread (the shared record
        must not be mutated off-loop) and the blocking stdout write+flush is
        offloaded to a single-thread executor so a slow stdout never stalls the
        loop. The executor is a worker-local created on first write and shut
        down in the finally, so each start/stop cycle gets a fresh executor.

        Returns:
            None
        """
        executor = _WriteExecutor()
        try:
            while self.running_event.is_set() or not self.queue.empty():
                try:
                    record = await asyncio.wait_for(self.queue.get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    formatter = self.formatter_provider()
                    if formatter:
                        text = formatter.format(record) + "\n"
                        await executor.run(lambda: self._write_stdout(text))
                except Exception as exc:
                    # A broken stdout or a buggy formatter must not silently kill
                    # the always-on console worker. Report the failure and keep
                    # draining so the queue is still acknowledged and shutdown
                    # completes.
                    self._report_error(record, exc)
                finally:
                    self.queue.task_done()
        finally:
            # The console owns no stream to close; just release the worker
            # thread. shutdown(wait=True) waits for any in-flight write.
            await executor.shutdown()

    def _write_stdout(self, text: str) -> None:
        """Write ``text`` to standard output and flush (runs on the executor thread)."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a console delivery error through the configured error channel.

        Delegates to the shared ``config.report_error`` helper (AR-108).
        """
        report_error("ConsoleBackend", self.error_handler, record, exc)

    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the console queue and return the outcome.

        Waits for every queued record to be acknowledged by the console worker,
        then returns a result describing how the drain concluded so the coordinator
        can surface it to the registered status reporters.

        Args:
            timeout (float): Timeout for draining the console queue.

        Returns:
            BackendDrainResult: How the console queue drained.
        """
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("console", DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult("console", DrainStatus.ERROR, exc)
        else:
            return BackendDrainResult("console", DrainStatus.COMPLETED)

    async def report_status(self, results: list[BackendDrainResult]) -> None:
        """
        Enqueue a synthetic status record for each backend's drain outcome.

        Called by the coordinator after all backends have drained, so the console
        surfaces how every backend fared during shutdown. Status records are
        best-effort: when the console queue is full they are dropped rather than
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
                # console queue is full, drop them rather than block shutdown on a
                # bounded queue that the worker may already be draining.
                pass
