"""
Asynchronous file logging handlers.

Provides AsyncFileHandler, a thin handler that registers a file backend on top
of the queue/worker machinery in AsyncLoggingHandler, plus the rotation variants
AsyncRotatingFileHandler, AsyncTimedRotatingFileHandler, and
AsyncWatchedFileHandler that mirror the standard-library handler names. The
backend (FileBackend and its rotation subclasses) owns the worker coroutine and
the entire file lifecycle; each handler is a thin constructor wrapper that
builds the right backend via `_make_backend` and registers its queue, worker,
and drain — exactly mirroring how `ConsoleHandler` registers `ConsoleBackend`.
"""

import logging
from collections.abc import Callable
from typing import Any

from ..async_logging_handler import _QUEUE_FILE, AsyncLoggingHandler
from ..backend.file import (
    FileBackend,
    RotatingFileBackend,
    TimedRotatingFileBackend,
    WatchedFileBackend,
)
from ..formatter.scietex import ScietexFormatter


class AsyncFileHandler(AsyncLoggingHandler):
    """
    Asynchronous file logging handler.

    Registers a ``"_file"`` backend that formats queued records and writes them
    to a file. The constructor mirrors the standard-library ``logging.FileHandler``
    signature (``filename``, ``mode``, ``encoding``, ``delay``, ``errors``) plus
    the scietex options (``error_handler``, ``queue_maxsize``, ``formatter``).

    The handler is a thin wrapper: it builds a `FileBackend` (which owns the
    worker coroutine and the entire file lifecycle — lazy open, write,
    close-in-finally) and registers the backend's queue, worker, and drain into
    the shared machinery, exactly mirroring how `ConsoleHandler` registers
    `ConsoleBackend`. The file handle is opened lazily by the backend worker and
    closed when the worker exits, so the file is only open while logging runs.
    An already-open file-like object may be injected via ``file=``; when one is
    provided the backend never closes it — the caller owns its lifetime and
    recovery (mirroring the broker ``client=`` injection seam). ``file=`` and
    ``filename`` are mutually exclusive.

    Attributes:
        _file_backend (FileBackend): The file sink, created in __init__.
    """

    # Always non-None: __init__ installs a default ScietexFormatter when no
    # formatter is passed, so the backend worker can format records without a
    # None guard.
    formatter: logging.Formatter

    def __init__(
        self,
        filename: str | None = None,
        *,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous file logging handler.

        Args:
            filename (str, optional): Path to the log file. Mutually exclusive
                with ``file``; omit it when injecting ``file=``.
            mode (str): File open mode (default "a").
            encoding (str, optional): File encoding (default None -> locale default).
            delay (bool): If True, defer opening the file until the first write
                (the backend worker opens it lazily). Defaults to False.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like
                object to write to. When provided, the handler never closes it —
                the caller owns its lifetime. Mutually exclusive with ``filename``.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a log record cannot be delivered.
            queue_maxsize (int): Maximum number of records each backend queue can
                hold. Defaults to 10000.
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``file`` and ``filename`` are provided.
        """
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
        self.formatter = formatter if formatter is not None else ScietexFormatter()
        self._file_backend: FileBackend = self._make_backend(
            filename=filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
        )
        # Register the backend's own worker and drain so the backend owns the
        # file lifecycle: it opens the file lazily, writes, and closes it in its
        # finally — exactly mirroring how ConsoleHandler registers ConsoleBackend.
        self.register_backend(
            _QUEUE_FILE,
            self._file_backend.queue,
            self._file_backend.worker,
            self._file_backend.drain,
        )
        self.register_status_reporter(self._file_backend.report_status)

    def _make_backend(
        self,
        *,
        filename: str | None,
        mode: str,
        encoding: str | None,
        delay: bool,
        errors: str | None,
        file: Any | None,
    ) -> FileBackend:
        """Build the file backend for this handler (overridden by rotation variants).

        Returns a `FileBackend` bound to this handler's formatter and running
        event. Rotation subclasses override this to return the matching backend
        subclass while forwarding their extra rotation options.
        """
        return FileBackend(
            lambda: self.formatter,
            self.logging_running_event,
            filename=filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )


class AsyncRotatingFileHandler(AsyncFileHandler):
    """
    Asynchronous rotating file logging handler.

    Mirrors ``logging.handlers.RotatingFileHandler``: when the file exceeds
    ``maxBytes``, the backend worker rolls it over, keeping ``backupCount``
    backups. Rollover is driven from the backend worker (the sole writer),
    never from ``emit()``.
    """

    def __init__(
        self,
        filename: str,
        *,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous rotating file logging handler.

        Args:
            filename (str): Path to the log file.
            mode (str): File open mode (default "a").
            maxBytes (int): Roll over when the file exceeds this many bytes.
                0 disables size-based rotation (default).
            backupCount (int): Number of backup files to keep (default 0).
            encoding (str, optional): File encoding.
            delay (bool): If True, defer opening the file until the first write.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like.
            error_handler (callable, optional): Delivery-error callback.
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        # Set the rotation options BEFORE super().__init__ so they are available
        # when the base __init__ calls the overridden _make_backend.
        self.maxBytes: int = maxBytes
        self.backupCount: int = backupCount
        super().__init__(
            filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )

    def _make_backend(
        self,
        *,
        filename: str | None,
        mode: str,
        encoding: str | None,
        delay: bool,
        errors: str | None,
        file: Any | None,
    ) -> FileBackend:
        """Build a `RotatingFileBackend` forwarding this handler's rotation options."""
        # Rotation handlers require a filename (their __init__ demands `filename`),
        # so the `str | None` the base signature carries is always a `str` here.
        assert filename is not None
        return RotatingFileBackend(
            lambda: self.formatter,
            self.logging_running_event,
            filename=filename,
            mode=mode,
            maxBytes=self.maxBytes,
            backupCount=self.backupCount,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )


class AsyncTimedRotatingFileHandler(AsyncFileHandler):
    """
    Asynchronous time-based rotating file logging handler.

    Mirrors ``logging.handlers.TimedRotatingFileHandler``: the backend worker
    rolls the file over on a time interval (``when``/``interval``), keeping
    ``backupCount`` backups. Rollover is driven from the backend worker, never
    from ``emit()``.
    """

    def __init__(
        self,
        filename: str,
        *,
        when: str = "h",
        interval: int = 1,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        utc: bool = False,
        atTime=None,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous timed rotating file logging handler.

        Args:
            filename (str): Path to the log file.
            when (str): Rollover interval type: 'S', 'M', 'H', 'D', 'W0'-'W6',
                or 'midnight' (default 'h').
            interval (int): Number of ``when`` units between rollovers (default 1).
            backupCount (int): Number of backup files to keep (default 0).
            encoding (str, optional): File encoding.
            delay (bool): If True, defer opening the file until the first write.
            utc (bool): Use UTC for rollover time computation (default False).
            atTime: Optional rollover time of day.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like.
            error_handler (callable, optional): Delivery-error callback.
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        # Set the rotation options BEFORE super().__init__ so they are available
        # when the base __init__ calls the overridden _make_backend.
        self.when: str = when
        self.interval: int = interval
        self.backupCount: int = backupCount
        self.utc: bool = utc
        self.atTime = atTime
        super().__init__(
            filename,
            mode="a",
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )

    def _make_backend(
        self,
        *,
        filename: str | None,
        mode: str,
        encoding: str | None,
        delay: bool,
        errors: str | None,
        file: Any | None,
    ) -> FileBackend:
        """Build a `TimedRotatingFileBackend` forwarding this handler's rotation options."""
        # Rotation handlers require a filename (their __init__ demands `filename`),
        # so the `str | None` the base signature carries is always a `str` here.
        assert filename is not None
        return TimedRotatingFileBackend(
            lambda: self.formatter,
            self.logging_running_event,
            filename=filename,
            when=self.when,
            interval=self.interval,
            backupCount=self.backupCount,
            encoding=encoding,
            delay=delay,
            utc=self.utc,
            atTime=self.atTime,
            errors=errors,
            file=file,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )


class AsyncWatchedFileHandler(AsyncFileHandler):
    """
    Asynchronous watched file logging handler.

    Mirrors ``logging.handlers.WatchedFileHandler``: the backend worker reopens
    the file if it was rotated or deleted externally (e.g. by logrotate), so
    logging continues to the new inode.
    """

    def __init__(
        self,
        filename: str,
        *,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous watched file logging handler.

        Args:
            filename (str): Path to the log file.
            mode (str): File open mode (default "a").
            encoding (str, optional): File encoding.
            delay (bool): If True, defer opening the file until the first write.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like.
            error_handler (callable, optional): Delivery-error callback.
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        super().__init__(
            filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )

    def _make_backend(
        self,
        *,
        filename: str | None,
        mode: str,
        encoding: str | None,
        delay: bool,
        errors: str | None,
        file: Any | None,
    ) -> FileBackend:
        """Build a `WatchedFileBackend` for this handler."""
        # Watched handlers require a filename (their __init__ demands `filename`),
        # so the `str | None` the base signature carries is always a `str` here.
        assert filename is not None
        return WatchedFileBackend(
            lambda: self.formatter,
            self.logging_running_event,
            filename=filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )
