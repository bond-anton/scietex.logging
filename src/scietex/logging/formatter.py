"""
Module with custom logging formatters.
"""

import copy
import logging
from datetime import datetime, timezone

# Imported from the shared stdlib-only leaf (AR-026); also re-exported here for
# backward compatibility with callers that import it from the formatter module.
from .config import level_abbreviation, resolve_instance_id


class ScietexFormatter(logging.Formatter):
    """
    Custom logging formatter for Scietex services.

    This formatter enriches log records with additional information such as
    the instance identifier, formats log levels into 3-letter abbreviations,
    and outputs timestamps in ISO format with UTC timezone by default.

    Attributes:
        worker_name (str): A formatted string that includes the service name and
            instance id.

    Methods:
        formatTime:
            Format the timestamp for the log record in ISO 8601 UTC by default.
        format:
            Format the specified log record as text with worker name and abbreviated levels.
    """

    def __init__(
        self,
        service_name: str,
        worker_id: int | None = None,
        instance_id: str | None = None,
        fmt: str | None = None,
        datefmt: str | None = None,
    ) -> None:
        """
        Initialize the ScietexFormatter instance.

        Args:
            service_name (str): The name of the service using this formatter.
            worker_id (int, optional): Deprecated identifier for the worker instance.
                Use ``instance_id`` instead; this parameter is removed in v2.0.
            instance_id (str, optional): Identifier for the logging instance.
                Defaults to "1". Mutually exclusive with ``worker_id``.
            fmt (str, optional): The log message format string. Defaults to
                "%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s".
            datefmt (str, optional): The date format string. Defaults to None.
        """
        resolved_instance_id = resolve_instance_id(worker_id, instance_id)
        if fmt is None:
            fmt = "%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s"
        super().__init__(fmt, datefmt)
        self.worker_name: str = f"{service_name}:{resolved_instance_id}"

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

        This method adds the worker name and converts the log level to a
        3-letter abbreviation before formatting.

        Args:
            record (logging.LogRecord): The log record to be formatted.

        Returns:
            str: The formatted log message string.
        """
        # Copy the record so the caller's record is never mutated.
        record = copy.copy(record)

        # Add the worker_name to the log record
        record.worker_name = self.worker_name

        # Convert the log level to a 3-letter abbreviation
        record.levelname = level_abbreviation(record.levelno)

        # Call the parent class's format method to perform the actual formatting
        return super().format(record)
