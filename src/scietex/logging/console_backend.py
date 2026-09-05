"""
Console backend for the asynchronous logging framework.

Encapsulates the console sink as a peer backend: it owns its queue, its worker
coroutine, and a reference to the handler's formatter. `AsyncBaseHandler`
registers this backend's queue and worker into the shared machinery the same
way `AsyncBrokerHandler` registers its broker queue and worker.
"""

import asyncio
import logging
import sys

from .async_logging_handler import BackendDrainResult, DrainStatus


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

    Attributes:
        queue (asyncio.Queue[logging.LogRecord]): Queue holding records destined
            for standard output.
        formatter (logging.Formatter | None): Formatter used to render records.
        running_event (asyncio.Event): Shared event signalling that logging is active.
    """

    def __init__(
        self,
        formatter: logging.Formatter | None,
        running_event: asyncio.Event,
        maxsize: int = 10000,
    ) -> None:
        """
        Initialize the console backend.

        Args:
            formatter (logging.Formatter | None): Formatter used to render records.
            running_event (asyncio.Event): Shared event signalling that logging is active.
            maxsize (int): Maximum number of records the queue can hold. Records
                enqueued past this bound are dropped by `emit`. Defaults to 10000.
        """
        self.queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(maxsize=maxsize)
        self.formatter = formatter
        self.running_event = running_event

    async def _worker(self) -> None:
        """
        Drain the console queue, writing formatted records to standard output.

        Continues as long as logging is active or records remain queued. The
        short timeout on `queue.get` lets the worker observe the running event
        being cleared without blocking forever.

        Returns:
            None
        """
        while self.running_event.is_set() or not self.queue.empty():
            try:
                record = await asyncio.wait_for(self.queue.get(), 1)
                if self.formatter:
                    sys.stdout.write(self.formatter.format(record) + "\n")
                    sys.stdout.flush()
                self.queue.task_done()
            except asyncio.TimeoutError:
                pass

    async def drain(self, timeout: float, results: list[BackendDrainResult]) -> None:
        """
        Report other backends' drain outcomes to the console, then drain our queue.

        For each drain result, a synthetic status record is queued so the console
        output surfaces how every other backend fared during shutdown. The console
        queue is then drained, flushing both the status records and any remaining
        application records.

        Args:
            timeout (float): Timeout for draining the console queue.
            results (list[BackendDrainResult]): Drain outcomes from other backends.

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
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except Exception:
            # A timeout or queue error must not abort shutdown; remaining records
            # are processed as the worker winds down below.
            pass
