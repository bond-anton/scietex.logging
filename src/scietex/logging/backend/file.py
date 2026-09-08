"""
File backend for the asynchronous logging framework.

Encapsulates the file sink as a peer backend: it owns its queue, its worker
coroutine, the entire file lifecycle (lazy open, write, close-in-finally), and
its shutdown-status reporting. `AsyncFileHandler` registers this backend's
queue, worker, and drain into the shared machinery the same way
`ConsoleHandler` registers the console backend.

The rotation subclasses (`RotatingFileBackend`, `TimedRotatingFileBackend`,
`WatchedFileBackend`) own the stdlib rollover/reopen logic and set
`_needs_record_copy` so the worker copies the shared record on the loop thread
before it crosses to the executor thread (the stdlib rotator's
`shouldRollover` formats the record internally, mutating it).
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

from .._executor import _WriteExecutor
from ..async_logging_handler import _QUEUE_FILE
from ._base import _QueueBackend


class FileBackend(_QueueBackend):
    """
    File sink for asynchronous log records.

    Owns an `asyncio.Queue`, a worker coroutine, and the entire file lifecycle:
    the file is opened lazily on first write, written to, and closed in the
    worker's `finally` on the same single-thread executor as the writes. It
    observes the handler's running event so it can wind down when logging stops.

    During shutdown the coordinator drains the file queue via `drain` and, after
    every backend has drained, invokes `report_status` to enqueue synthetic status
    records describing how each backend fared. The file backend is therefore a
    post-drain observer, not a drain hook that reads a shared results list
    mid-iteration.

    The rotation subclasses set the class attribute `_needs_record_copy` to
    ``True`` so the worker copies the shared record on the loop thread before
    handing it to the executor (the stdlib rotator's `shouldRollover` formats
    the record internally, which would mutate the shared record off-loop). The
    error channel always receives the ORIGINAL record, never the copy.

    Attributes:
        queue (asyncio.Queue[logging.LogRecord]): Queue holding records destined
            for the file.
        formatter_provider (Callable[[], logging.Formatter | None]): Zero-arg
            callable returning the handler's current formatter, read at work time
            so the backend never holds a stale copy.
        running_event (asyncio.Event): Shared event signalling that logging is active.
        filename (str | None): Path to the log file; mutually exclusive with ``file``.
        mode (str): File open mode (default "a").
        encoding (str | None): File encoding.
        delay (bool): Unused for lazy-open semantics (the worker always opens
            lazily); retained for stdlib-signature parity.
        errors (str | None): Encoding error handling scheme.
        error_handler (Callable | None): Optional callback invoked with
            ``(record, exc)`` when a record cannot be written to the file.
    """

    _queue_name = _QUEUE_FILE
    _backend_name = "FileBackend"

    # Rotation subclasses set this True so the worker copies the record on the
    # loop thread before it crosses to the executor (the stdlib rotator's
    # shouldRollover formats it internally, mutating it).
    _needs_record_copy: bool = False

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        *,
        filename: str | None = None,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """
        Initialize the file backend.

        Args:
            formatter_provider (Callable[[], logging.Formatter | None]): Zero-arg
                callable returning the handler's current formatter. Stored so the
                worker reads the formatter live rather than a stale copy.
            running_event (asyncio.Event): Shared event signalling that logging is active.
            filename (str, optional): Path to the log file. Mutually exclusive
                with ``file``; omit it when injecting ``file=``.
            mode (str): File open mode (default "a").
            encoding (str, optional): File encoding (default None -> locale default).
            delay (bool): Accepted for stdlib-signature parity; the worker always
                opens the file lazily on first write regardless of this value.
            errors (str, optional): Encoding error handling scheme.
            file (Any | None): An externally-managed, already-open file-like
                object to write to. When provided, the backend never closes it —
                the caller owns its lifetime. Mutually exclusive with ``filename``.
            maxsize (int): Maximum number of records the queue can hold. Records
                enqueued past this bound are dropped by `emit`. Defaults to 10000.
            error_handler (callable, optional): Callback invoked with
                ``(record, exc)`` when a record cannot be written. Defaults to
                None, in which case errors are reported via the ``scietex.logging``
                module logger.

        Raises:
            ValueError: If both ``file`` and ``filename`` are provided.
        """
        super().__init__(
            formatter_provider,
            running_event,
            maxsize=maxsize,
            error_handler=error_handler,
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

    def _open_stream(self) -> Any:
        """Open the file handle lazily, honoring injected-file ownership.

        When a file-like was injected (``_owns_file`` is False), the backend
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

        When a file-like was injected, the backend never closes a file it does
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

    def _write_record(self, text: str, record: logging.LogRecord | None = None) -> None:
        """Write ``text`` to the current stream and flush (runs on the executor thread).

        The stream is opened lazily here (on the executor thread) so all stream
        access stays on the single executor thread. ``record`` is ignored by the
        plain backend; the rotation subclasses override this to drive rollover.
        The optional default keeps the signature uniform across subclasses so the
        unified worker can always pass ``(text, record)``.
        """
        stream = self._open_stream()
        stream.write(text)
        stream.flush()

    async def _worker(self) -> None:
        """
        Drain the file queue, writing formatted records to the file.

        Opens the file lazily on first use, then loops draining the queue and
        writing formatted records. Each record is formatted on the event-loop
        thread (the shared record must not be mutated off-loop) and the blocking
        write+flush is offloaded to a single-thread executor so a slow filesystem
        never stalls the loop. Rotation subclasses copy the record on the loop
        thread (`_needs_record_copy`) before it crosses to the executor.

        The file is closed in a ``finally`` on the *same* executor thread,
        serialized strictly after any in-flight write, so a worker cancelled
        mid-write never closes the stream under a live write (no write-after-
        close). The executor is a worker-local shut down in the finally, so each
        start/stop cycle gets a fresh executor and the backend stays restartable.

        Returns:
            None
        """
        executor = _WriteExecutor()
        try:
            while self.running_event.is_set() or not self.queue.empty():
                try:
                    record = await asyncio.wait_for(self.queue.get(), 1)
                except asyncio.TimeoutError:
                    continue
                try:
                    formatter = self.formatter_provider()
                    if formatter:
                        text = formatter.format(record) + "\n"
                        write_record = copy.copy(record) if self._needs_record_copy else record
                        await executor.run(lambda: self._write_record(text, write_record))
                except Exception as exc:
                    # A broken file or a buggy formatter must not silently kill
                    # the file worker. Report the failure (with the ORIGINAL
                    # record, not any copy) and keep draining so the queue is
                    # still acknowledged and shutdown completes.
                    self._report_error(record, exc)
                finally:
                    self.queue.task_done()
        finally:
            # Release the file whether the worker exits normally or is cancelled.
            # Submit _close_stream to the SAME single-thread executor so it is
            # serialized strictly after any in-flight write (no write-after-close),
            # then wait for the executor to finish and release its thread.
            await executor.run(self._close_stream)
            await executor.shutdown()


class RotatingFileBackend(FileBackend):
    """
    Size-based rotating file backend.

    Mirrors ``logging.handlers.RotatingFileHandler``: when the file exceeds
    ``maxBytes`` the worker rolls it over, keeping ``backupCount`` backups.
    Rollover is driven from the worker (the sole writer), never from ``emit``.
    """

    _needs_record_copy = True

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        *,
        filename: str,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """Initialize the size-based rotating file backend.

        Args mirror ``AsyncRotatingFileHandler``'s rotation options; ``filename``
        is required. The stdlib rotator is constructed with ``delay=True`` and is
        never used to emit — it only supplies `shouldRollover`/`doRollover`.
        """
        super().__init__(
            formatter_provider,
            running_event,
            filename=filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=maxsize,
            error_handler=error_handler,
        )
        self.maxBytes: int = maxBytes
        self.backupCount: int = backupCount
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
        the shared record if the original were passed across threads.
        """
        stream = self._open_stream()
        # Point the stdlib rotator at our live stream so its
        # shouldRollover/doRollover operate on the real handle.
        self._rotator.stream = stream
        # The worker always passes a copy; the None default exists only so this
        # override stays signature-compatible with FileBackend._write_record.
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


class TimedRotatingFileBackend(FileBackend):
    """
    Time-based rotating file backend.

    Mirrors ``logging.handlers.TimedRotatingFileHandler``: the worker rolls the
    file over on a time interval (``when``/``interval``), keeping
    ``backupCount`` backups. Rollover is driven from the worker, never from
    ``emit``.
    """

    _needs_record_copy = True

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        *,
        filename: str,
        when: str = "h",
        interval: int = 1,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        utc: bool = False,
        atTime=None,
        errors: str | None = None,
        file: Any | None = None,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """Initialize the time-based rotating file backend.

        Args mirror ``AsyncTimedRotatingFileHandler``'s rotation options;
        ``filename`` is required. The stdlib rotator is constructed with
        ``delay=True`` and is never used to emit.
        """
        super().__init__(
            formatter_provider,
            running_event,
            filename=filename,
            mode="a",
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=maxsize,
            error_handler=error_handler,
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
        invariant holds. ``record`` is a copy made on the event-loop thread. The
        stdlib ``TimedRotatingFileHandler.shouldRollover`` ignores its record
        argument (it only compares the current time against ``rolloverAt``), but
        the record is still passed through to satisfy the stdlib
        ``shouldRollover(record: LogRecord)`` type contract.
        """
        stream = self._open_stream()
        # Point the stdlib rotator at our live stream so its
        # shouldRollover/doRollover operate on the real handle.
        self._rotator.stream = stream
        # The worker always passes a copy; the None default exists only so this
        # override stays signature-compatible with FileBackend._write_record.
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


class WatchedFileBackend(FileBackend):
    """
    Watched file backend.

    Mirrors ``logging.handlers.WatchedFileHandler``: the worker reopens the file
    if it was rotated or deleted externally (e.g. by logrotate), so logging
    continues to the new inode. Inherits ``_needs_record_copy = False`` (no
    record copy is required — the stdlib watcher does not format the record).
    """

    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        *,
        filename: str,
        mode: str = "a",
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        file: Any | None = None,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        """Initialize the watched file backend.

        Args mirror ``AsyncWatchedFileHandler``'s options; ``filename`` is
        required. The stdlib watcher is constructed with ``delay=True`` and is
        never used to emit — it only supplies ``reopenIfNeeded``.
        """
        super().__init__(
            formatter_provider,
            running_event,
            filename=filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            maxsize=maxsize,
            error_handler=error_handler,
        )
        self._watcher = WatchedFileHandler(
            filename,
            mode=mode,
            encoding=encoding,
            delay=True,
            errors=errors,
        )

    def _write_record(self, text: str, record: logging.LogRecord | None = None) -> None:
        """Reopen the file if it was rotated externally, then write ``text``.

        Runs as one atomic unit on the executor thread so the sole-writer
        invariant holds. ``record`` is accepted for signature compatibility with
        the unified worker but ignored (the stdlib watcher does not format it).
        """
        stream = self._open_stream()
        # Point the stdlib watcher at our live stream and ask it to reopen if
        # the file was replaced externally (logrotate). reopenIfNeeded returns
        # None, so adopt whatever stream the watcher now holds — the original or
        # a freshly opened handle.
        self._watcher.stream = stream
        self._watcher.reopenIfNeeded()
        self._stream = self._watcher.stream
        # Re-fetch the live stream: reopenIfNeeded may have closed the previous
        # handle and opened a fresh one we just adopted.
        stream = self._open_stream()
        stream.write(text)
        stream.flush()
