"""
Asynchronous base handler with a console logging backend.

Provides AsyncBaseHandler, a concrete handler that adds a console backend on
top of the queue/worker machinery in AsyncLoggingHandler.
"""

import logging
from collections.abc import Callable

from .async_logging_handler import AsyncLoggingHandler
from .config import RedisConfig, ValkeyConfig
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
        stdout_enable (bool): Flag to enable console logging (defaults to True);
            read-only alias for `config.stdout_enable`.
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
        queue_maxsize: int = 10000,
        backend_config: RedisConfig | ValkeyConfig | None = None,
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
            queue_maxsize (int): Maximum number of records each backend queue can
                hold. Defaults to 10000.
            backend_config (RedisConfig | ValkeyConfig | None): Backend-specific
                config forwarded by broker subclasses. Defaults to None.

        Attributes:
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            error_handler (callable | None): Callback for reporting delivery errors.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            stdout_enable=stdout_enable,
            backend_config=backend_config,
        )
        self._console_backend: ConsoleBackend | None = None
        if self.config.stdout_enable:
            self._console_backend = ConsoleBackend(
                self.formatter,
                self.logging_running_event,
                maxsize=self.config.queue_maxsize,
            )
            self.register_backend(
                "console",
                self._console_backend.queue,
                self._console_backend._worker,
                self._console_backend.drain,
            )
            self.register_status_reporter(self._console_backend.report_status)

    @property
    def stdout_enable(self) -> bool:
        """Read-only alias for ``config.stdout_enable``."""
        return self.config.stdout_enable

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        """
        Set the formatter used to render log records.

        The console backend captures the formatter reference when the handler is
        constructed, so it must be updated here too for console output to reflect
        the change. Broker backends read the handler's formatter dynamically and
        need no extra handling.

        Args:
            fmt (logging.Formatter | None): The formatter to use.
        """
        super().setFormatter(fmt)
        if self._console_backend is not None:
            self._console_backend.formatter = fmt
