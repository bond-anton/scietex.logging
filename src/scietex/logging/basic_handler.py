"""
Asynchronous base handler with a console logging backend.

Provides AsyncBaseHandler, a concrete handler that adds a console backend on
top of the queue/worker machinery in AsyncLoggingHandler.
"""

import logging
from collections.abc import Callable

from .async_logging_handler import AsyncLoggingHandler
from .console_backend import ConsoleBackend


class AsyncBaseHandler(AsyncLoggingHandler):
    """
    Asynchronous base handler with a console logging backend.

    Overview:
        This handler builds on the `AsyncLoggingHandler` machinery and registers
        a console backend that outputs log messages to standard output. The
        console backend can be disabled via `stdout_enable`, and additional
        backends (such as Redis or Valkey) can be added in subclasses.

    Attributes:
        stdout_enable (bool): Flag to enable console logging (defaults to True).
        _console_backend (ConsoleBackend | None): Console sink, created when
            `stdout_enable` is True; otherwise None.
    """

    def __init__(
        self,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous base logging handler.

        Args:
            service_name (str, optional): Name of the service for log identification.
                Defaults to "Service".
            worker_id (int, optional): Identifier for the worker instance. Defaults to 1.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a log record cannot be delivered. Defaults to
                None, in which case errors are reported via the ``scietex.logging``
                module logger.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            **kwargs: Additional keyword arguments passed through to the base machinery.

        Attributes:
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            error_handler (callable | None): Callback for reporting delivery errors.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            **kwargs,
        )
        self.stdout_enable = stdout_enable
        self._console_backend: ConsoleBackend | None = None
        if self.stdout_enable:
            self._console_backend = ConsoleBackend(self.formatter, self.logging_running_event)
            self.register_backend(
                "console",
                self._console_backend.queue,
                self._console_backend._worker,
                self._console_backend.drain,
            )
