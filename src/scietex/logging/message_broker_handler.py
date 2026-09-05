"""Asynchronous logging handler for non-blocking logging to message broker."""

import abc
import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .async_logging_handler import BackendDrainResult, DrainStatus
from .basic_handler import AsyncBaseHandler
from .config import RedisConfig, ValkeyConfig
from .formatter import level_abbreviation


class AsyncBrokerHandler(AsyncBaseHandler, abc.ABC):
    """
    Abstract asynchronous logging handler for non-blocking logging to a message broker.

    This handler sends log records to a message broker, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process log records queued in an asyncio queue.

    Subclasses must implement `connect()`, `disconnect()`, and `send_message()`.

    Attributes:
        queue_name (str): The name of the queue for the handler.
        client (Any | None): The client for sending logs to broker, or None if not connected.

    Methods:
        connect():
            Connect to message broker asynchronously.
        disconnect():
            Disconnect from message broker asynchronously.
        send_message():
            Send message to message broker asynchronously.
        _worker():
            Worker to retrieve and send log records from the queue to broker.
    """

    def __init__(
        self,
        queue_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        backend_config: RedisConfig | ValkeyConfig | None = None,
    ) -> None:
        """
        Initialize the asynchronous Message broker logging handler.

        Args:
            queue_name (str): The name of the queue from which log records are read.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            error_handler (callable, optional): Callback invoked with ``(record, exc)``
                when a log record cannot be delivered. Defaults to None, in which case
                errors are reported via the ``scietex.logging`` module logger.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
                Defaults to 10000.
            backend_config (RedisConfig | ValkeyConfig | None): Backend-specific config
                attached by concrete broker subclasses. Defaults to None.

        Attributes:
            queue_name (str): The name of the queue for the handler.
            client (Any | None): The client for sending logs to broker, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            backend_config=backend_config,
        )
        self.queue_name: str = queue_name
        self.client: Any | None = None
        self.register_backend(
            self.queue_name,
            asyncio.Queue(maxsize=self.config.queue_maxsize),
            self._worker,
            self.drain,
        )

    @abc.abstractmethod
    async def connect(self) -> None:
        """
        Connect to the message broker asynchronously.

        Subclasses must establish the broker client and set `self.client`. A failure
        must raise so the worker can report it and retry.

        Returns:
            None
        """
        ...

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from the message broker asynchronously.

        Subclasses must close the broker client and reset `self.client` to None.

        Returns:
            None
        """
        ...

    @abc.abstractmethod
    async def send_message(self, record: dict[str, str]) -> None:
        """
        Send a log record to the message broker asynchronously.

        ``record`` is a serializable log entry: a ``dict[str, str]`` mapping the keys
        ``level``, ``message``, ``name``, and ``time`` to their string values. Each
        concrete adapter translates the entry to the argument shape its client expects
        (e.g. Redis ``xadd`` accepts the dict directly, while Valkey-glide ``xadd``
        expects ``record.items()``). A failure must raise so the worker can report it
        via the error channel and acknowledge the queue task; the record is dropped,
        not retried.

        Args:
            record (dict[str, str]): The log record to send, keyed by ``level``,
                ``message``, ``name``, and ``time``.

        Returns:
            None
        """
        ...

    async def _worker(self) -> None:
        """
        Asynchronous worker to handle logging to Message broker.

        Retrieves log records from the queue, formats them, and sends them
        to the Message broker. The worker continues running
        as long as logging is active or there are records in the queue.

        Returns:
            None
        """
        try:
            while (
                self.logging_running_event.is_set() or not self.log_queues[self.queue_name].empty()
            ):
                if self.client is None:
                    try:
                        await self.connect()
                    except Exception as exc:
                        self._report_error(None, exc)
                        await asyncio.sleep(1.0)
                        continue
                try:
                    record = await asyncio.wait_for(self.log_queues[self.queue_name].get(), 1)
                except asyncio.TimeoutError:
                    continue
                # Compute the broker fields from the handler identity (config) and the
                # record directly, not from formatter internals. A plain
                # logging.Formatter installed via setFormatter has no worker_name
                # attribute and a non-ISO formatTime, so deriving name/time from the
                # formatter would silently change the broker wire format. The identity
                # lives on config, and time is always ISO-8601 UTC regardless of any
                # custom formatter/datefmt.
                level = level_abbreviation(record.levelno)
                name = f"{self.config.service_name}:{self.config.worker_id}"
                log_entry: dict[str, str] = {
                    "level": level,
                    "message": record.getMessage(),
                    "name": name,
                    "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                }
                try:
                    await self.send_message(log_entry)
                except Exception as exc:
                    # A send failure usually means the connection dropped
                    # mid-stream: the client is now unusable, so tear it down and
                    # force a reconnect next iteration instead of reusing the dead
                    # client (which would drop every subsequent record). The record
                    # was already dequeued by get(); ack the processing attempt so
                    # queue.join() can complete. Visibility of the drop comes from
                    # _report_error, not from withholding task_done().
                    self._report_error(record, exc)
                    try:
                        await self.disconnect()
                    except Exception:
                        # disconnect() itself failed (e.g. the transport is already
                        # gone); drop the stale reference so connect() re-runs.
                        self.client = None
                finally:
                    self.log_queues[self.queue_name].task_done()
        finally:
            # Release the client whether the worker exits normally or is cancelled
            # (e.g. by stop_logging's gather-bound cancel while the worker is idle in
            # queue.get()). Clearing the reference unconditionally keeps the teardown
            # idempotent even when disconnect() raises on a half-closed transport.
            try:
                await self.disconnect()
            except Exception as exc:
                self._report_error(None, exc)
            self.client = None

    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the broker queue and return the outcome for status reporting.

        Waits for every queued record to be acknowledged by the worker, then returns
        a result describing how the drain concluded so the coordinator can surface it
        to the registered status reporters (e.g. the console).

        Args:
            timeout (float): Timeout for the queue to drain.

        Returns:
            BackendDrainResult: How the drain concluded.
        """
        try:
            await asyncio.wait_for(self.log_queues[self.queue_name].join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult(self.queue_name, DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult(self.queue_name, DrainStatus.ERROR, exc)
        else:
            return BackendDrainResult(self.queue_name, DrainStatus.COMPLETED)
