"""Console, Redis, and Valkey backends on a single logger example."""

import asyncio
import logging

from glide import GlideClientConfiguration, NodeAddress

from scietex.logging import AsyncBaseHandler, AsyncRedisHandler, AsyncValkeyHandler


async def main():
    """Main function."""
    logger = logging.getLogger("AllBackendsLogger")
    logger.setLevel(logging.DEBUG)

    console_handler = AsyncBaseHandler(service_name="AllService", worker_id=1)
    redis_handler = AsyncRedisHandler(
        stream_name="all_backends_stream",
        service_name="AllService",
        worker_id=2,
        redis_config={"host": "localhost", "port": 6379, "db": 0},
        stdout_enable=False,
    )
    valkey_handler = AsyncValkeyHandler(
        stream_name="all_backends_stream",
        service_name="AllService",
        worker_id=3,
        valkey_config=GlideClientConfiguration([NodeAddress("localhost", 6379)]),
        stdout_enable=False,
    )
    logger.addHandler(console_handler)
    logger.addHandler(redis_handler)
    logger.addHandler(valkey_handler)

    # Only the console handler keeps stdout_enable=True; the broker handlers disable
    # it so each record is emitted to the console once rather than three times.
    await console_handler.start_logging()
    await redis_handler.start_logging()
    await valkey_handler.start_logging()

    logger.info("Info message to all backends.")
    logger.error("Error message to all backends.")

    await console_handler.stop_logging()
    await redis_handler.stop_logging()
    await valkey_handler.stop_logging()


asyncio.run(main())
