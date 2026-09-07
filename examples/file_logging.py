"""Plain-text and JSON file logging example."""

import asyncio
import logging

from scietex.logging import AsyncFileHandler, JsonFormatter


async def main():
    """Main function."""
    logger = logging.getLogger("FileLogger")
    logger.setLevel(logging.DEBUG)

    # Plain-text file handler (console output disabled so records go to the file only).
    plain_handler = AsyncFileHandler(
        "plain.log",
        service_name="FileService",
        instance_id="1",
        stdout_enable=False,
    )

    # JSON file handler: one JSON object per line.
    json_handler = AsyncFileHandler(
        "json.log",
        service_name="FileService",
        instance_id="2",
        formatter=JsonFormatter(),
        stdout_enable=False,
    )

    logger.addHandler(plain_handler)
    logger.addHandler(json_handler)

    await plain_handler.start_logging()
    await json_handler.start_logging()

    logger.info("Info message to both files.")
    logger.error("Error message to both files.")

    await plain_handler.stop_logging()
    await json_handler.stop_logging()

    print("Wrote plain.log (plain text) and json.log (JSON lines).")


asyncio.run(main())
