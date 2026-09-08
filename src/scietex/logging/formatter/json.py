"""
Structured JSON logging formatter.

`JsonFormatter` renders a log record as a single-line JSON object, suitable for
file sinks and log aggregators that consume newline-delimited JSON (NDJSON).
"""

import copy
import json
import logging

from ..config import iso_timestamp

# The stdlib LogRecord default attribute names, captured once from a bare record.
# JsonFormatter flattens only user-added `extra` fields, so these (plus the keys
# JsonFormatter emits itself) are excluded from the JSON line.
_STDLIB_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys())


class JsonFormatter(logging.Formatter):
    """
    Format log records as single-line JSON objects.

    Each record becomes one JSON object with the keys ``timestamp`` (ISO-8601
    UTC), ``level`` (full level name, e.g. ``"INFO"``), ``logger`` (the record's
    logger name), ``message`` (the rendered message), and an ``exception`` key
    (the traceback as a single string) present only when the record carries
    ``exc_info``. Any user-added ``extra`` fields on the record are flattened
    into the object as top-level keys.

    The record is copied before formatting so the caller's shared ``LogRecord``
    is never mutated (the same record is fanned out to every backend queue).
    Values that are not JSON-serializable degrade to their ``repr`` string via
    ``json.dumps(default=repr)`` rather than raising in the worker.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the record as a single-line JSON object.

        Args:
            record (logging.LogRecord): The log record to format.

        Returns:
            str: A single-line JSON object describing the record.
        """
        # Copy the record so the caller's record is never mutated (shared-record
        # fan-out discipline, mirroring ScietexFormatter.format).
        record = copy.copy(record)

        data: dict = {
            "timestamp": iso_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)

        # Flatten only user-added extras: skip our own keys, the stdlib LogRecord
        # attributes, and any private attribute.
        for key, value in record.__dict__.items():
            if key not in data and key not in _STDLIB_ATTRS and not key.startswith("_"):
                data[key] = value

        # default=repr keeps a non-serializable extra value from crashing the
        # worker; it degrades to its repr string instead.
        return json.dumps(data, default=repr)
