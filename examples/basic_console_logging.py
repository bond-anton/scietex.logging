"""AsyncBaseHandler usage example."""

# examples/basic_console_logging.py

import logging
import asyncio
from scietex.logging import AsyncBaseHandler


async def main():
    """Main function."""
    # Initialize a logger
    logger = logging.getLogger("ExampleLogger")
    logger.setLevel(logging.DEBUG)

    # Set up asynchronous logging handler with console output
    async_handler = AsyncBaseHandler(
        service_name="MyService", worker_id=1, stdout_enable=True
    )
    logger.addHandler(async_handler)

    # Start the asynchronous logging tasks
    await async_handler.start_logging()

    # Log some messages at various levels
    logger.debug("This is a debug message.")
    logger.info("This is an info message.")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")
    logger.critical("This is a critical message.")

    # Stop the logging and cleanup
    await async_handler.stop_logging()


# Run the example
asyncio.run(main())
