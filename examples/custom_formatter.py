"""ScietexFormatter customization and setFormatter example."""

import asyncio
import logging

from scietex.logging import AsyncBaseHandler, ScietexFormatter


async def main():
    """Main function."""
    logger = logging.getLogger("FormatterLogger")
    logger.setLevel(logging.DEBUG)

    handler = AsyncBaseHandler(service_name="FormatterService", worker_id=1)
    logger.addHandler(handler)

    # The "|" separators and the non-ISO datefmt are the two visible changes this
    # formatter introduces relative to the handler's default ScietexFormatter.
    formatter = ScietexFormatter(
        service_name="FormatterService",
        worker_id=1,
        fmt="%(asctime)s | %(levelname)s | [%(worker_name)s] | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # setFormatter replaces the handler's formatter for the console (stdout)
    # sink only. Broker backends build their payloads from the handler's
    # service_name/worker_id config and the record directly, so they are
    # invariant under setFormatter. This example uses AsyncBaseHandler, whose
    # only sink is the console, so the custom layout appears in its output.
    handler.setFormatter(formatter)

    await handler.start_logging()

    logger.info("This info message uses the custom format.")
    logger.error("This error message uses the custom format.")

    await handler.stop_logging()


asyncio.run(main())
