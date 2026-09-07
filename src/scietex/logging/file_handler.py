"""
Asynchronous file logging handlers.

Provides AsyncFileHandler, a concrete handler that registers a file backend on
top of the queue/worker machinery in AsyncBaseHandler, plus the rotation
variants AsyncRotatingFileHandler, AsyncTimedRotatingFileHandler, and
AsyncWatchedFileHandler that mirror the standard-library handler names.
"""

import asyncio
import copy
import logging
from collections.abc import Callable
from logging.handlers import (
    RotatingFileHandler,
    TimedRotatingFileHandler,
    WatchedFileHandler,
)
from typing import Any

from ._executor import _WriteExecutor
from .async_logging_handler import BackendDrainResult, DrainStatus
from .basic_handler import AsyncBaseHandler
from .file_backend import FileBackend


class AsyncFileHandler(AsyncBaseHandler):
    """
    Asynchronous file logging handler.

    Registers a ``"file"`` backend that formats queued records and writes them
    to a file. The constructor mirrors the standard-library ``logging.FileHandler``
    signature (``filename``, ``mode``, ``encoding``, ``delay``, ``errors``) plus
    the scietex options (``service_name``, ``worker_id``, ``error_handler``,
    ``stdout_enable``, ``queue_maxsize``, ``formatter``).

    The file handle is opened lazily by the worker (respecting ``delay``) and
    closed when the worker exits, so the file is only open while logging runs.
    An already-open file-like object may be injected via ``file=``; when one is
    provided the handler never closes it — the caller owns its lifetime and
    recovery (mirroring the broker ``client=`` injection seam). ``file=`` and
    ``filename`` are mutually exclusive.

    Attributes:
        _file_backend (FileBackend | None): The file sink, created in __init__.
        _owns_file (bool): True when the handler opened its own file and must
            close it; False when an external file-like was injected.
        _injected_file (Any | None): The externally-managed file-like, when one
            was injected; otherwise None.
    """

    # Always non-None: AsyncLoggingHandler.__init__ installs a default
    # ScietexFormatter when no formatter is passed, so the worker can format
    # records without a None guard.
    formatter: logging.Formatter

    def __init__(
        self,
        filename: str | None = None,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous file logging handler.

        Args:
            filename (str, optional): Path to the log file. Mutually exclusive
                with ``file``; omit it when injecting ``file=``.
            service_name (str, optional): Name of the service for log identification.
            worker_id (int, optional): Identifier for the worker instance.
            mode (str): File open mode (default "a").
            encoding (str, optional): File encoding (default None -> locale default).
            delay (bool): If True, defer opening the file until the first write
                (the worker opens it lazily). Defaults to False.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like
                object to write to. When provided, the handler never closes it —
                the caller owns its lifetime. Mutually exclusive with ``filename``.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a log record cannot be delivered.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can
                hold. Defaults to 10000.
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed from ``service_name`` and ``worker_id``.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``file`` and ``filename`` are provided.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )
        if file is not None and filename is not None:
            raise ValueError(
                "file and filename are mutually exclusive: pass an injected "
                "file-like OR a filename for the handler to open, not both."
            )
        self.filename: str | None = filename
        self.mode: str = mode
        self.encoding: str | None = encoding
        self.delay: bool = delay
        self.errors: str | None = errors
        self._owns_file: bool = file is None
        self._injected_file: Any | None = file
        self._stream: Any | None = file  # the current write target (None until opened)
        self._file_backend: FileBackend | None = FileBackend(
            lambda: self.formatter,
            self.logging_running_event,
            lambda: self._stream,
            maxsize=self.config.queue_maxsize,
            error_handler=self._report_error,
        )
        # Register the handler's own worker and drain (not the FileBackend's) so
        # the worker owns the file lifecycle: it opens the file lazily, writes,
        # and closes it in its finally. The FileBackend supplies the queue and the
        # shutdown status reporter.
        self.register_backend(
            "file",
            self._file_backend.queue,
            self._worker,
            self.drain,
        )
        self.register_status_reporter(self._file_backend.report_status)

    def _open_stream(self) -> Any:
        """Open the file handle lazily, honoring injected-file ownership.

        When a file-like was injected (``_owns_file`` is False), the handler
        never opens its own file: it restores the durable injected reference and
        returns. Otherwise it opens ``self.filename`` with the configured mode,
        encoding, and errors.
        """
        if not self._owns_file:
            self._stream = self._injected_file
            return self._stream
        if self._stream is None:
            filename = self.filename
            # Owning the file (not injecting one) requires a filename to open.
            assert filename is not None
            self._stream = open(
                filename,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
            )
        return self._stream

    def _close_stream(self) -> None:
        """Close the file handle, honoring injected-file ownership.

        When a file-like was injected, the handler never closes a file it does
        not own — the host owns its lifetime — so this is a no-op. Otherwise it
        closes the handle it opened and resets the reference.
        """
        if not self._owns_file:
            return
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None

    def _write_record(self, text: str) -> None:
        """Write ``text`` to the current stream and flush (runs on the executor thread).

        The stream is opened lazily here (on the executor thread) so all stream
        access stays on the single executor thread.
        """
        stream = self._open_stream()
        stream.write(text)
        stream.flush()

    async def _worker(self) -> None:
        """
        Asynchronous worker that drains the file queue and writes to the file.

        Opens the file lazily on first use (respecting ``delay``), then loops
        draining the queue and writing formatted records. Each record is
        formatted on the event-loop thread (the shared record must not be
        mutated off-loop) and the blocking write+flush is offloaded to a
        single-thread executor so a slow filesystem never stalls the loop.

        The file is closed in a ``finally`` on the *same* executor thread,
        serialized strictly after any in-flight write, so a worker cancelled
        mid-write never closes the stream under a live write (no write-after-
        close). The executor is a worker-local shut down in the finally, so each
        start/stop cycle gets a fresh executor and the handler stays restartable.

        Returns:
            None
        """
        executor = _WriteExecutor()
        try:
            while self.logging_running_event.is_set() or not self.log_queues["file"].empty():
                try:
                    record = await asyncio.wait_for(self.log_queues["file"].get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    text = self.formatter.format(record) + "\n"
                    await executor.run(lambda: self._write_record(text))
                except Exception as exc:
                    # A broken file or a buggy formatter must not silently kill
                    # the file worker. Report the failure and keep draining so
                    # the queue is still acknowledged and shutdown completes.
                    self._report_error(record, exc)
                finally:
                    self.log_queues["file"].task_done()
        finally:
            # Release the file whether the worker exits normally or is cancelled.
            # Submit _close_stream to the SAME single-thread executor so it is
            # serialized strictly after any in-flight write (no write-after-close),
            # then wait for the executor to finish and release its thread.
            await executor.run(self._close_stream)
            await executor.shutdown()

    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the file queue and return the outcome for status reporting.

        Args:
            timeout (float): Timeout for the queue to drain.

        Returns:
            BackendDrainResult: How the drain concluded.
        """
        try:
            await asyncio.wait_for(self.log_queues["file"].join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("file", DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult("file", DrainStatus.ERROR, exc)
        else:
            return BackendDrainResult("file", DrainStatus.COMPLETED)


class AsyncRotatingFileHandler(AsyncFileHandler):
    """
    Asynchronous rotating file logging handler.

    Mirrors ``logging.handlers.RotatingFileHandler``: when the file exceeds
    ``maxBytes``, the worker rolls it over, keeping ``backupCount`` backups.
    Rollover is driven from the worker (the sole writer), never from ``emit()``.
    """

    def __init__(
        self,
        filename: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous rotating file logging handler.

        Args:
            filename (str): Path to the log file.
            service_name (str, optional): Name of the service for log identification.
            worker_id (int, optional): Identifier for the worker instance.
            mode (str): File open mode (default "a").
            maxBytes (int): Roll over when the file exceeds this many bytes.
                0 disables size-based rotation (default).
            backupCount (int): Number of backup files to keep (default 0).
            encoding (str, optional): File encoding.
            delay (bool): If True, defer opening the file until the first write.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like.
            error_handler (callable, optional): Delivery-error callback.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        super().__init__(
            filename,
            service_name=service_name,
            worker_id=worker_id,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )
        self.maxBytes: int = maxBytes
        self.backupCount: int = backupCount
        # Reuse the stdlib rotation logic. The stdlib handler is never used to
        # emit; it only supplies shouldRollover/doRollover/rotation_filename.
        # delay=True keeps it from opening its own handle — the worker points it
        # at the handler's live stream instead.
        self._rotator = RotatingFileHandler(
            filename,
            mode=mode,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=True,
            errors=errors,
        )

    def _write_record(self, text: str, record: logging.LogRecord | None = None) -> None:
        """Roll over if the file exceeds maxBytes, then write ``text``.

        Runs as one atomic unit on the executor thread so the sole-writer
        invariant holds (all stream mutation happens on the single executor
        thread). ``record`` is a copy made on the event-loop thread: the stdlib
        rotator's ``shouldRollover`` formats it internally, which would mutate
        the shared record if the original were passed across threads. The
        optional default keeps this override signature-compatible with the base
        ``AsyncFileHandler._write_record(text)``.
        """
        stream = self._open_stream()
        # Point the stdlib rotator at our live stream so its
        # shouldRollover/doRollover operate on the real handle.
        self._rotator.stream = stream
        # The worker always passes a copy; the None default exists only so this
        # override stays signature-compatible with AsyncFileHandler._write_record.
        assert record is not None
        if self._rotator.shouldRollover(record):
            self._rotator.doRollover()
            # doRollover closed the previous stream and, because the rotator is
            # delay=True, left its stream None. Reopen for the current record so
            # it lands in the freshly-rotated file.
            self._stream = None
            stream = self._open_stream()
        stream.write(text)
        stream.flush()

    async def _worker(self) -> None:
        """
        Worker that writes records and rolls the file over when it exceeds maxBytes.

        Each record is formatted on the event-loop thread and the blocking
        rollover+write unit is offloaded to a single-thread executor. The file
        is closed on the same executor thread in the finally (serialized after
        any in-flight write), and the executor is shut down so the handler stays
        restartable.

        Returns:
            None
        """
        executor = _WriteExecutor()
        try:
            while self.logging_running_event.is_set() or not self.log_queues["file"].empty():
                try:
                    record = await asyncio.wait_for(self.log_queues["file"].get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    text = self.formatter.format(record) + "\n"
                    # Copy the record on the loop thread so the rotator's internal
                    # format (inside shouldRollover) never mutates the shared
                    # record on the executor thread.
                    record_copy = copy.copy(record)
                    await executor.run(lambda: self._write_record(text, record_copy))
                except Exception as exc:
                    self._report_error(record, exc)
                finally:
                    self.log_queues["file"].task_done()
        finally:
            # Close on the SAME single-thread executor (serialized strictly after
            # any in-flight write), then wait for the executor to finish.
            await executor.run(self._close_stream)
            await executor.shutdown()


class AsyncTimedRotatingFileHandler(AsyncFileHandler):
    """
    Asynchronous time-based rotating file logging handler.

    Mirrors ``logging.handlers.TimedRotatingFileHandler``: the worker rolls the
    file over on a time interval (``when``/``interval``), keeping ``backupCount``
    backups. Rollover is driven from the worker, never from ``emit()``.
    """

    def __init__(
        self,
        filename: str,
        service_name: str | None = None,
        worker_id: int | None = None,
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
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous timed rotating file logging handler.

        Args:
            filename (str): Path to the log file.
            service_name (str, optional): Name of the service for log identification.
            worker_id (int, optional): Identifier for the worker instance.
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
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        super().__init__(
            filename,
            service_name=service_name,
            worker_id=worker_id,
            mode="a",
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )
        self.when: str = when
        self.interval: int = interval
        self.backupCount: int = backupCount
        self.utc: bool = utc
        self.atTime = atTime
        self._rotator = TimedRotatingFileHandler(
            filename,
            when=when,
            interval=interval,
            backupCount=backupCount,
            encoding=encoding,
            delay=True,
            utc=utc,
            atTime=atTime,
            errors=errors,
        )

    def _write_record(self, text: str, record: logging.LogRecord | None = None) -> None:
        """Roll over if the rollover time has passed, then write ``text``.

        Runs as one atomic unit on the executor thread so the sole-writer
        invariant holds (all stream mutation happens on the single executor
        thread). ``record`` is a copy made on the event-loop thread. The stdlib
        ``TimedRotatingFileHandler.shouldRollover`` ignores its record argument
        (it only compares the current time against ``rolloverAt``), but the
        optional ``record`` is still passed through to satisfy the stdlib
        ``shouldRollover(record: LogRecord)`` type contract and to keep this
        override signature-compatible with ``AsyncFileHandler._write_record(text)``.
        """
        stream = self._open_stream()
        # Point the stdlib rotator at our live stream so its
        # shouldRollover/doRollover operate on the real handle.
        self._rotator.stream = stream
        # The worker always passes a copy; the None default exists only so this
        # override stays signature-compatible with AsyncFileHandler._write_record.
        assert record is not None
        if self._rotator.shouldRollover(record):
            self._rotator.doRollover()
            # doRollover closed the previous stream and, because the rotator is
            # delay=True, left its stream None. Reopen for the current record so
            # it lands in the freshly-rotated file.
            self._stream = None
            stream = self._open_stream()
        stream.write(text)
        stream.flush()

    async def _worker(self) -> None:
        """
        Worker that writes records and rolls the file over on a time interval.

        Each record is formatted on the event-loop thread and the blocking
        rollover+write unit is offloaded to a single-thread executor. The file
        is closed on the same executor thread in the finally (serialized after
        any in-flight write), and the executor is shut down so the handler stays
        restartable.

        Returns:
            None
        """
        executor = _WriteExecutor()
        try:
            while self.logging_running_event.is_set() or not self.log_queues["file"].empty():
                try:
                    record = await asyncio.wait_for(self.log_queues["file"].get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    text = self.formatter.format(record) + "\n"
                    # Copy the record on the loop thread so the stdlib rotator's
                    # shouldRollover never touches the shared record on the
                    # executor thread (mirrors AsyncRotatingFileHandler).
                    record_copy = copy.copy(record)
                    await executor.run(lambda: self._write_record(text, record_copy))
                except Exception as exc:
                    self._report_error(record, exc)
                finally:
                    self.log_queues["file"].task_done()
        finally:
            # Close on the SAME single-thread executor (serialized strictly after
            # any in-flight write), then wait for the executor to finish.
            await executor.run(self._close_stream)
            await executor.shutdown()


class AsyncWatchedFileHandler(AsyncFileHandler):
    """
    Asynchronous watched file logging handler.

    Mirrors ``logging.handlers.WatchedFileHandler``: the worker reopens the file
    if it was rotated or deleted externally (e.g. by logrotate), so logging
    continues to the new inode.
    """

    def __init__(
        self,
        filename: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous watched file logging handler.

        Args:
            filename (str): Path to the log file.
            service_name (str, optional): Name of the service for log identification.
            worker_id (int, optional): Identifier for the worker instance.
            mode (str): File open mode (default "a").
            encoding (str, optional): File encoding.
            delay (bool): If True, defer opening the file until the first write.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like.
            error_handler (callable, optional): Delivery-error callback.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
            formatter (logging.Formatter | None): Formatter used to render records.
        """
        super().__init__(
            filename,
            service_name=service_name,
            worker_id=worker_id,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            formatter=formatter,
        )
        self._watcher = WatchedFileHandler(
            filename,
            mode=mode,
            encoding=encoding,
            delay=True,
            errors=errors,
        )

    async def _worker(self) -> None:
        """
        Worker that writes records and reopens the file if it was rotated externally.

        Returns:
            None
        """
        try:
            while self.logging_running_event.is_set() or not self.log_queues["file"].empty():
                try:
                    record = await asyncio.wait_for(self.log_queues["file"].get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    stream = self._open_stream()
                    # Point the stdlib watcher at our live stream and ask it to
                    # reopen if the file was replaced externally (logrotate).
                    # reopenIfNeeded returns None, so adopt whatever stream the
                    # watcher now holds — the original or a freshly opened handle.
                    self._watcher.stream = stream
                    self._watcher.reopenIfNeeded()
                    self._stream = self._watcher.stream
                    # Re-fetch the live stream: reopenIfNeeded may have closed the
                    # previous handle and opened a fresh one we just adopted.
                    stream = self._open_stream()
                    stream.write(self.formatter.format(record) + "\n")
                    stream.flush()
                except Exception as exc:
                    self._report_error(record, exc)
                finally:
                    self.log_queues["file"].task_done()
        finally:
            self._close_stream()
