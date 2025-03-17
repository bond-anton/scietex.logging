"""AsyncRedisHandler usage example."""

# examples/redis_logging.py

import logging
import asyncio
from scietex.logging.redis_handler import AsyncRedisHandler


async def main():
    """Main function."""
    # Initialize a logger
    logger = logging.getLogger("ExampleRedisLogger")
    logger.setLevel(logging.DEBUG)

    # Set up the asynchronous Redis logging handler
    redis_handler = AsyncRedisHandler(
        stream_name="example_log_stream",
        service_name="RedisService",
        worker_id=2,
        redis_config={"host": "localhost", "port": 6379},
    )
    logger.addHandler(redis_handler)

    # Start the asynchronous logging tasks
    await redis_handler.start_logging()

    # Log some messages at various levels
    logger.info("This is an info message sent to Redis.")
    logger.error("This is an error message sent to Redis.")

    # Stop the logging and cleanup
    await redis_handler.stop_logging()


# Run the example
asyncio.run(main())
