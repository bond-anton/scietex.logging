"""Drop-and-report overflow with a custom error_handler example."""

import asyncio
import logging

from scietex.logging import AsyncBaseHandler


def on_error(record, exc):
    """Report a record that could not be delivered."""
    # repr renders the exception type (QueueFull has an empty str()).
    print(f"[error_handler] dropped record: {exc!r}")


async def main():
    """Main function."""
    logger = logging.getLogger("OverflowLogger")
    logger.setLevel(logging.DEBUG)

    # queue_maxsize=2 bounds the console queue; when it is full, emit drops new
    # records and routes them to error_handler instead of blocking the producer.
    handler = AsyncBaseHandler(
        service_name="OverflowService",
        worker_id=1,
        queue_maxsize=2,
        error_handler=on_error,
    )
    logger.addHandler(handler)

    await handler.start_logging()

    # The burst outruns the console worker, so emit drops records once the bounded
    # queue fills. The exact drop count depends on how quickly the worker drains,
    # so this demonstrates the mechanism without asserting a specific number.
    for i in range(20):
        logger.info(f"Burst record {i}")

    await handler.stop_logging()


asyncio.run(main())
