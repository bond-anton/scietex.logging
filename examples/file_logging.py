"""Plain-text and JSON file logging example."""

import asyncio
import logging

from scietex.logging import AsyncFileHandler, JsonFormatter


async def main():
    """Main function."""
    logger = logging.getLogger("FileLogger")
    logger.setLevel(logging.DEBUG)

    # Plain-text file handler (file-only handler; add a ConsoleHandler separately
    # if console output is wanted).
    plain_handler = AsyncFileHandler(
        "plain.log",
    )

    # JSON file handler: one JSON object per line. Both handlers share the
    # "FileLogger" logger, so both write the same record.name.
    json_handler = AsyncFileHandler(
        "json.log",
        formatter=JsonFormatter(),
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
