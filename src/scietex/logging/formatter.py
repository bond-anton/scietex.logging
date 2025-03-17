"""
Module with custom logging formatters.
"""

from typing import Union
from datetime import datetime, timezone
import logging


def level_abbreviation(log_level: int) -> str:
    """
    Map logging levels to 3-letter abbreviations.

    Args:
        log_level (int): The integer log level (e.g., logging.DEBUG, logging.INFO).

    Returns:
        str: A 3-letter abbreviation corresponding to the log level, or a 3-digit code
             if the level is unrecognized.
    """
    level_map: dict[int, str] = {
        logging.DEBUG: "DBG",  # type: ignore
        logging.INFO: "INF",  # type: ignore
        logging.WARNING: "WRN",  # type: ignore
        logging.ERROR: "ERR",  # type: ignore
        logging.CRITICAL: "CRT",  # type: ignore
    }
    return level_map.get(log_level, f"{log_level:03d}")


class NTSFormatter(logging.Formatter):  # type: ignore
    """
    Custom logging formatter for NTS services.

    This formatter enriches log records with additional information such as
    the worker identifier, formats log levels into 3-letter abbreviations,
    and outputs timestamps in ISO format with UTC timezone by default.

    Attributes:
        worker_name (str): A formatted string that includes the service name and worker ID.
    """

    def __init__(
        self,
        service_name: str,
        worker_id: Union[int, None] = None,
        fmt: Union[str, None] = None,
        datefmt: Union[str, None] = None,
    ) -> None:
        """
        Initialize the NTSFormatter instance.

        Args:
            service_name (str): The name of the service using this formatter.
            worker_id (int, optional): The worker ID. Defaults to 1 if not specified.
            fmt (str, optional): The log message format string. Defaults to a
                                 specific format if not provided.
            datefmt (str, optional): The date format string. Defaults to None.
        """
        if worker_id is None:
            worker_id = 1
        if fmt is None:
            fmt = "%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s"
        super().__init__(fmt, datefmt)
        self.worker_name: str = f"{service_name}:{worker_id}"

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

    def format(self, record: logging.LogRecord) -> str:  # type: ignore
        """
        Format the specified log record as text.

        This method adds the worker name and converts the log level to a
        3-letter abbreviation before formatting.

        Args:
            record (logging.LogRecord): The log record to be formatted.

        Returns:
            str: The formatted log message string.
        """
        # Add the worker_name to the log record
        record.worker_name = self.worker_name

        # Convert the log level to a 3-letter abbreviation
        record.levelname = level_abbreviation(record.levelno)

        # Call the parent class's format method to perform the actual formatting
        return super().format(record)
