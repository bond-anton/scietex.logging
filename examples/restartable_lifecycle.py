"""Restartable handler lifecycle example."""

import asyncio
import logging

from scietex.logging import AsyncBaseHandler


async def main():
    """Main function."""
    logger = logging.getLogger("LifecycleLogger")
    logger.setLevel(logging.DEBUG)

    handler = AsyncBaseHandler(service_name="LifecycleService", worker_id=1)
    logger.addHandler(handler)

    # Cycle 1: start, log, stop with a custom drain timeout.
    await handler.start_logging()
    logger.info("First cycle message.")
    await handler.stop_logging(timeout=2.0)

    # stop_logging is idempotent: a second call while already stopped is a no-op.
    await handler.stop_logging()

    # Cycle 2: each start_logging schedules fresh worker tasks from a clean queue.
    # Any record logged between stop and the next start is dropped, because
    # stop_logging cleared the accept event.
    await handler.start_logging()
    logger.info("Second cycle message.")
    await handler.stop_logging()

    # Starting while already running raises RuntimeError.
    await handler.start_logging()
    try:
        await handler.start_logging()
    except RuntimeError as exc:
        print(f"double start rejected: {exc}")
    await handler.stop_logging()


asyncio.run(main())
