"""AsyncBaseHandler and AsyncRedisHandler simultaneous usage example."""

# examples/console_and_redis_logging.py

import logging
import asyncio
from scietex.logging.basic_handler import AsyncBaseHandler
from scietex.logging.redis_handler import AsyncRedisHandler


async def main():
    """Main function."""
    # Initialize a logger
    logger = logging.getLogger("CombinedLogger")
    logger.setLevel(logging.DEBUG)

    # Set up asynchronous console logging handler
    console_handler = AsyncBaseHandler(
        service_name="CombinedService", worker_id=1, stdout_enable=True
    )
    logger.addHandler(console_handler)

    # Set up asynchronous Redis logging handler
    redis_handler = AsyncRedisHandler(
        stream_name="combined_log_stream",
        service_name="CombinedService",
        worker_id=2,
        redis_config={"host": "localhost", "port": 6379},
        stdout_enable=True,
    )
    logger.addHandler(redis_handler)

    # Start both logging handlers
    await console_handler.start_logging()
    await redis_handler.start_logging()

    # Log some messages at various levels
    logger.info("Info message to both console and Redis.")
    logger.warning("Warning message to both console and Redis.")
    logger.error("Error message to both console and Redis.")

    # Stop both logging handlers
    await console_handler.stop_logging()
    await redis_handler.stop_logging()


# Run the example
asyncio.run(main())
