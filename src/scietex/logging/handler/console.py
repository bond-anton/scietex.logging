"""
Asynchronous handler with a console logging backend.

Provides ConsoleHandler, a concrete handler that adds a console backend on
top of the queue/worker machinery in AsyncLoggingHandler.
"""

import logging
from collections.abc import Callable

from ..async_logging_handler import _QUEUE_CONSOLE, AsyncLoggingHandler
from ..backend.console import ConsoleBackend
from ..formatter.scietex import ScietexFormatter


class ConsoleHandler(AsyncLoggingHandler):
    """
    Asynchronous handler with a console logging backend.

    Overview:
        This handler builds on the `AsyncLoggingHandler` machinery and registers
        a console backend that outputs log messages to standard output.

    Attributes:
        _console_backend (ConsoleBackend): Console sink, always constructed.
    """

    def __init__(
        self,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous console logging handler.

        Args:
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a log record cannot be delivered. Defaults to
                None, in which case errors are reported via the ``scietex.logging``
                module logger.
            queue_maxsize (int): Maximum number of records each backend queue can
                hold. Defaults to 10000.
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed.

        Attributes:
            error_handler (callable | None): Callback for reporting delivery errors.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
        self.formatter = formatter if formatter is not None else ScietexFormatter()
        self._console_backend = ConsoleBackend(
            lambda: self.formatter,
            self.logging_running_event,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )
        self.register_backend(
            _QUEUE_CONSOLE,
            self._console_backend.queue,
            self._console_backend.worker,
            self._console_backend.drain,
        )
        self.register_status_reporter(self._console_backend.report_status)
