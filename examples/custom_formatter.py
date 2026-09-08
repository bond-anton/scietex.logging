"""ScietexFormatter customization and setFormatter example."""

import asyncio
import logging

from scietex.logging import ConsoleHandler, ScietexFormatter


async def main():
    """Main function."""
    logger = logging.getLogger("FormatterLogger")
    logger.setLevel(logging.DEBUG)

    handler = ConsoleHandler()
    logger.addHandler(handler)

    # The "|" separators and the non-ISO datefmt are the two visible changes this
    # formatter introduces relative to the handler's default ScietexFormatter.
    # %(name)s renders the record's logger name, which carries the identity now.
    formatter = ScietexFormatter(
        fmt="%(asctime)s | %(levelname)s | [%(name)s] | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # setFormatter replaces the handler's formatter for the console (stdout)
    # sink only. Broker handlers no longer accept a formatter at all — their
    # payloads are built from the record directly (including record.name). This
    # example uses ConsoleHandler, whose only sink is the console, so the
    # custom layout appears in its output.
    handler.setFormatter(formatter)

    await handler.start_logging()

    logger.info("This info message uses the custom format.")
    logger.error("This error message uses the custom format.")

    await handler.stop_logging()


asyncio.run(main())
