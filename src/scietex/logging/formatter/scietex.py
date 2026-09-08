"""
Module with custom logging formatters.
"""

import copy
import logging
from datetime import datetime, timezone

# Imported from the shared stdlib-only leaf (AR-026); also re-exported here for
# backward compatibility with callers that import it from the formatter module.
from ..config import level_abbreviation


class ScietexFormatter(logging.Formatter):
    """
    Custom logging formatter for Scietex services.

    This formatter formats log levels into 3-letter abbreviations and outputs
    timestamps in ISO format with UTC timezone by default. Identity comes solely
    from the standard library logger name, read from ``record.name`` via the
    ``%(name)s`` format token.

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
    ) -> None:
        """
        Initialize the ScietexFormatter instance.

        Args:
            fmt (str, optional): The log message format string. Defaults to
                "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s", where
                ``%(name)s`` reads the record's standard library logger name.
            datefmt (str, optional): The date format string. Defaults to None.
        """
        if fmt is None:
            fmt = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"
        super().__init__(fmt, datefmt)

    def formatTime(self, record, datefmt=None):
        """
        Format the timestamp for the log record in ISO 8601 UTC by default.

        Args:
            record (logging.LogRecord): The log record for which to format the timestamp.
            datefmt (str, optional): The date format string. Defaults to None.

        Returns:
            str: A formatted timestamp string. Defaults to ISO 8601 with UTC timezone if
                 no datefmt is specified.
        """
        if datefmt is None:
            dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
            return dt.isoformat()
        return super().formatTime(record, datefmt)

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the specified log record as text.

        This method converts the log level to a 3-letter abbreviation before
        formatting. The record's identity is its standard library logger name
        (``record.name``), rendered by the ``%(name)s`` format token.

        Args:
            record (logging.LogRecord): The log record to be formatted.

        Returns:
            str: The formatted log message string.
        """
        # Copy the record so the caller's record is never mutated.
        record = copy.copy(record)

        # Convert the log level to a 3-letter abbreviation
        record.levelname = level_abbreviation(record.levelno)

        # Call the parent class's format method to perform the actual formatting
        return super().format(record)
