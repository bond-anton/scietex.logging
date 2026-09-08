"""Custom backend built directly on AsyncLoggingHandler machinery example."""

import asyncio
import logging

from scietex.logging import AsyncLoggingHandler, ScietexFormatter
from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus


class FileLikeHandler(AsyncLoggingHandler):
    """Handler that formats records into an in-memory list of lines."""

    # Always non-None: __init__ installs a default ScietexFormatter when no
    # formatter is passed, so the worker can format records without a None guard.
    formatter: logging.Formatter

    def __init__(self, *args, formatter: logging.Formatter | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.formatter = formatter if formatter is not None else ScietexFormatter()
        self.written: list[str] = []
        # AsyncLoggingHandler registers no backend itself; this handler registers a
        # single "filelike" backend whose worker formats records into self.written.
        self.register_backend("filelike", asyncio.Queue(), self._worker, self.drain)

    async def _worker(self):
        while self.logging_running_event.is_set() or not self.log_queues["filelike"].empty():
            try:
                record = await asyncio.wait_for(self.log_queues["filelike"].get(), 1)
            except asyncio.TimeoutError:
                continue
            self.written.append(self.formatter.format(record))
            self.log_queues["filelike"].task_done()

    async def drain(self, timeout: float) -> BackendDrainResult:
        try:
            await asyncio.wait_for(self.log_queues["filelike"].join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("filelike", DrainStatus.TIMEOUT)
        return BackendDrainResult("filelike", DrainStatus.COMPLETED)


async def main():
    """Main function."""
    logger = logging.getLogger("PureLogger")
    logger.setLevel(logging.DEBUG)

    handler = FileLikeHandler()
    logger.addHandler(handler)

    await handler.start_logging()

    logger.info("Info message through the custom backend.")
    logger.error("Error message through the custom backend.")

    await handler.stop_logging()

    for line in handler.written:
        print(line)


asyncio.run(main())
