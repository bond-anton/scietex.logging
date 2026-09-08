"""Console, Redis, and Valkey backends on a single logger example."""

import asyncio
import logging

from scietex.logging import AsyncRedisHandler, AsyncValkeyHandler, ConsoleHandler


async def main():
    """Main function."""
    logger = logging.getLogger("AllBackendsLogger")
    logger.setLevel(logging.DEBUG)

    console_handler = ConsoleHandler()
    redis_handler = AsyncRedisHandler(
        stream_name="all_backends_stream",
        redis_config={"host": "localhost", "port": 6379, "db": 0},
    )
    valkey_handler = AsyncValkeyHandler(
        stream_name="all_backends_stream",
        valkey_config={"addresses": [("localhost", 6379)]},
    )
    logger.addHandler(console_handler)
    logger.addHandler(redis_handler)
    logger.addHandler(valkey_handler)

    await console_handler.start_logging()
    await redis_handler.start_logging()
    await valkey_handler.start_logging()

    logger.info("Info message to all backends.")
    logger.error("Error message to all backends.")

    await console_handler.stop_logging()
    await redis_handler.stop_logging()
    await valkey_handler.stop_logging()


asyncio.run(main())
