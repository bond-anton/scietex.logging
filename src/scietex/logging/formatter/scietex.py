"""
Provides `ScietexFormatter`, a formatter that renders abbreviated 3-letter
levels, ISO-8601 UTC timestamps, and optional ANSI theming.
"""

import copy
import logging

# Imported from the shared stdlib-only leaf (AR-026); also re-exported here for
# backward compatibility with callers that import it from the formatter module.
from ..config import iso_timestamp, level_abbreviation
from ..theme import BOLD, DIM, MONOCHROME, RESET, LoggingTheme, ansi_bg, ansi_fg


def _paint(
    text: str,
    color: str | None,
    *,
    bold: bool = False,
    bg: str | None = None,
) -> str:
    """Wrap ``text`` in ANSI styling sequences, or return it plain when unstyled.

    Args:
        text (str): The text to style.
        color (str | None): Foreground hex color, or None to leave it uncolored.
        bold (bool): Whether to prepend the bold SGR sequence.
        bg (str | None): Background hex color, or None to leave it unset.

    Returns:
        str: The styled text, or ``text`` unchanged when no style is requested.
    """
    prefixes: list[str] = []
    if bold:
        prefixes.append(BOLD)
    if color is not None:
        prefixes.append(ansi_fg(color))
    if bg is not None:
        prefixes.append(ansi_bg(bg))
    if not prefixes:
        return text
    return "".join(prefixes) + text + RESET


class ScietexFormatter(logging.Formatter):
    """
    Custom logging formatter for Scietex services.

    This formatter formats log levels into 3-letter abbreviations and outputs
    timestamps in ISO format with UTC timezone by default. Identity comes solely
    from the standard library logger name, read from ``record.name`` via the
    ``%(name)s`` format token.

    Color is opt-in: with ``theme=None`` (the default) the output is monochrome
    and byte-for-byte identical to a plain formatter. Passing a ``theme`` and
    ``color=True`` paints the level abbreviation, logger name, message, and
    timestamp with the theme's ANSI palette.

    Methods:
        formatTime:
            Format the timestamp for the log record in ISO 8601 UTC by default.
        format:
            Format the specified log record as text with abbreviated levels.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        *,
        theme: LoggingTheme | None = None,
        color: bool | None = None,
    ) -> None:
        """
        Initialize the ScietexFormatter instance.

        Args:
            fmt (str, optional): The log message format string. Defaults to
                "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s", where
                ``%(name)s`` reads the record's standard library logger name.
            datefmt (str, optional): The date format string. Defaults to None.
            theme (LoggingTheme, optional): The color theme to apply. Defaults to
                the monochrome theme, which emits no ANSI sequences.
            color (bool, optional): Explicit color on/off override. Defaults to
                None, which defers to ``theme.color``.
        """
        if fmt is None:
            fmt = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
        super().__init__(fmt, datefmt)
        self._theme = theme if theme is not None else MONOCHROME
        self._color = self._theme.color if color is None else color

    def formatTime(self, record, datefmt=None):
        """
        Format the timestamp for the log record in ISO 8601 UTC by default.

        Args:
            record (logging.LogRecord): The log record for which to format the timestamp.
            datefmt (str, optional): The date format string. Defaults to None.

        Returns:
            str: A formatted timestamp string. Defaults to ISO 8601 with UTC timezone if
                 no datefmt is specified. When color is enabled the timestamp is wrapped
                 in a dim ANSI sequence.
        """
        if datefmt is None:
            timestamp = iso_timestamp(record.created)
        else:
            timestamp = super().formatTime(record, datefmt)
        if self._color:
            return DIM + timestamp + RESET
        return timestamp

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the specified log record as text.

        This method converts the log level to a 3-letter abbreviation before
        formatting. The record's identity is its standard library logger name
        (``record.name``), rendered by the ``%(name)s`` format token. When color
        is enabled, the level abbreviation, logger name, and message are painted
        with the theme's palette before the parent formatter runs.

        Args:
            record (logging.LogRecord): The log record to be formatted.

        Returns:
            str: The formatted log message string.
        """
        # Copy the record so the caller's record is never mutated.
        record = copy.copy(record)

        if self._color:
            palette = self._theme.palette
            painted_message = _paint(record.getMessage(), palette.foreground)
            record.levelname = _paint(
                level_abbreviation(record.levelno),
                palette.level_color(record.levelno),
                bold=True,
                bg=palette.critical_bg if record.levelno == logging.CRITICAL else None,
            )
            record.name = _paint(record.name, palette.logger_name)
            record.msg = painted_message
            record.args = None
            return super().format(record)

        # Convert the log level to a 3-letter abbreviation
        record.levelname = level_abbreviation(record.levelno)

        # Call the parent class's format method to perform the actual formatting
        return super().format(record)
