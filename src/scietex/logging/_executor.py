"""
Single-thread executor helper for offloading blocking write I/O.

The Console and File workers perform blocking ``write``/``flush`` (and, for the
rotation handlers, rollover/reopen) directly on the event-loop thread. This
module provides a tiny wrapper around a lazily-created
``ThreadPoolExecutor(max_workers=1)`` so those blocking calls run on a dedicated
worker thread instead of stalling the loop.

The executor is single-threaded so every write (and the final close) is strictly
serialized: a close submitted after an in-flight write cannot overtake it, which
is what prevents a write-after-close race during shutdown. It is created lazily
on first use so a handler that is constructed but never started leaks no thread,
and it is shut down (``wait=True``) at the end of each worker run so the handler
stays restartable across start/stop cycles.
"""

import asyncio
import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


class _WriteExecutor:
    """Lazily-created single-thread executor serializing blocking write I/O."""

    def __init__(self) -> None:
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def _get(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        return self._executor

    async def run(self, fn: Callable[[], _T]) -> _T:
        """Run ``fn`` on the single worker thread and await its completion.

        ``fn`` performs the blocking I/O for one record (write/flush, or the
        full rollover+reopen+write sequence). Formatting must happen on the
        event-loop thread *before* calling this; only blocking I/O belongs in
        ``fn``.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get(), fn)

    async def shutdown(self) -> None:
        """Wait for any in-flight work, then release the worker thread.

        Safe to call when the executor was never created (a no-op). After this
        returns, no write or close is still running on the worker thread, so the
        handler can be restarted with a fresh executor.
        """
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
