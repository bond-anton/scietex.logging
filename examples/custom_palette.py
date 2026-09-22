"""Custom color palette and theme example."""

import asyncio
import logging

from scietex.logging import ConsoleHandler, LoggingTheme, Palette

# The palette is the extension point: a user defines any hex colors they like and
# wraps them in a LoggingTheme without touching the library. Only background and
# foreground are required; every per-level slot defaults to None (uncolored).
ocean_palette = Palette(
    background="#002B36",
    foreground="#93A1A1",
    debug="#586E75",
    info="#2AA198",
    warning="#B58900",
    error="#DC322F",
    critical="#FDF6E3",
    critical_bg="#DC322F",
    logger_name="#268BD2",
)

ocean_theme = LoggingTheme(name="ocean", palette=ocean_palette, color=True)


async def main():
    """Main function."""
    logger = logging.getLogger("OceanLogger")
    logger.setLevel(logging.DEBUG)

    # color=True forces ANSI output even when stdout is piped.
    handler = ConsoleHandler(theme=ocean_theme, color=True)
    logger.addHandler(handler)

    await handler.start_logging()

    logger.debug("This is a debug message.")
    logger.info("This is an info message.")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")
    logger.critical("This is a critical message.")

    await handler.stop_logging()


asyncio.run(main())
