"""Built-in Scietex color theme (light and dark) example."""

import asyncio
import logging

from scietex.logging import SCIETEX_DARK, SCIETEX_LIGHT, ConsoleHandler


async def main():
    """Main function."""
    # color=True forces ANSI output so the example stays colored even when stdout
    # is piped; omit it and color is auto-detected from the terminal (sys.stdout).
    dark_logger = logging.getLogger("ScietexDark")
    dark_logger.setLevel(logging.DEBUG)
    dark_handler = ConsoleHandler(theme=SCIETEX_DARK, color=True)
    dark_logger.addHandler(dark_handler)

    light_logger = logging.getLogger("ScietexLight")
    light_logger.setLevel(logging.DEBUG)
    light_handler = ConsoleHandler(theme=SCIETEX_LIGHT, color=True)
    light_logger.addHandler(light_handler)

    await dark_handler.start_logging()
    await light_handler.start_logging()

    dark_logger.debug("Dark theme debug message.")
    dark_logger.info("Dark theme info message.")
    dark_logger.warning("Dark theme warning message.")
    dark_logger.error("Dark theme error message.")
    dark_logger.critical("Dark theme critical message.")

    light_logger.debug("Light theme debug message.")
    light_logger.info("Light theme info message.")
    light_logger.warning("Light theme warning message.")
    light_logger.error("Light theme error message.")
    light_logger.critical("Light theme critical message.")

    await dark_handler.stop_logging()
    await light_handler.stop_logging()


asyncio.run(main())
