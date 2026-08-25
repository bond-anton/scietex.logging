"""AsyncValkeyHandler usage example."""

import asyncio
import logging

from scietex.logging.valkey_handler import AsyncValkeyHandler


async def main():
    """Main function."""
    logger = logging.getLogger("ExampleValkeyLogger")
    logger.setLevel(logging.DEBUG)

    valkey_handler = AsyncValkeyHandler(
        stream_name="example_log_stream",
        service_name="ValkeyService",
        worker_id=3,
    )
    logger.addHandler(valkey_handler)

    await valkey_handler.start_logging()

    logger.info("This is an info message sent to Valkey.")
    logger.error("This is an error message sent to Valkey.")

    await valkey_handler.stop_logging()


asyncio.run(main())
