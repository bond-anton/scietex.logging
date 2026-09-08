"""
Console backend for the asynchronous logging framework.

Encapsulates the console sink as a peer backend: it owns its queue, its worker
coroutine, and a provider for the handler's formatter. `ConsoleHandler`
registers this backend's queue and worker into the shared machinery the same
way `AsyncBrokerHandler` registers its broker queue and worker.
"""

import asyncio
import logging
import sys
from collections.abc import Callable

from .._executor import _WriteExecutor
from ..async_logging_handler import _QUEUE_CONSOLE
from ._base import _QueueBackend


class ConsoleBackend(_QueueBackend):
    """
    Console sink for asynchronous log records.

    Owns an `asyncio.Queue` and a worker coroutine that formats queued records
    and writes them to standard output. It observes the handler's running event
    so it can wind down when logging stops.

    During shutdown the coordinator drains the console queue via `drain` and, after
    every backend has drained, invokes `report_status` to enqueue synthetic status
    records describing how each backend fared. The console is therefore a post-drain
    observer, not a drain hook that reads a shared results list mid-iteration.

    The shared queue/drain/status-reporting machinery lives in `_QueueBackend`;
    only the console-specific ``_worker``/``_write_stdout`` pair is defined here.

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

    _queue_name = _QUEUE_CONSOLE
    _backend_name = "ConsoleBackend"

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
        super().__init__(
            formatter_provider,
            running_event,
            maxsize=maxsize,
            error_handler=error_handler,
        )

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
