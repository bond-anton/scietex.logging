"""Asynchronous logging handler for non-blocking logging to message broker."""

import abc
import asyncio
import logging
import random
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .async_logging_handler import BackendDrainResult, DrainStatus
from .basic_handler import AsyncBaseHandler
from .config import RedisConfig, ValkeyConfig, level_abbreviation

# Connect-retry backoff (AR-022). A fixed 1s retry would spam the error channel
# ~3600x/hour during a prolonged outage. Consecutive connect() failures sleep a
# delay that doubles from a 0.5s base up to a 30s cap; ±20% jitter on each sleep
# decorrelates retries across workers, and a successful connect resets the
# counter to base so a flap does not resume at the capped delay.
_CONNECT_RETRY_BASE = 0.5
_CONNECT_RETRY_CAP = 30.0
_CONNECT_RETRY_JITTER = 0.2


class AsyncBrokerHandler(AsyncBaseHandler, abc.ABC):
    """
    Abstract asynchronous logging handler for non-blocking logging to a message broker.

    This handler sends log records to a message broker, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process log records queued in an asyncio queue.

    Subclasses must implement `connect()`, `disconnect()`, and `send_message()`.

    An externally-managed client may be injected via the ``client`` keyword
    argument; when one is provided the handler never calls ``close()`` on it —
    the caller owns its lifetime and recovery.

    Attributes:
        queue_name (str): The name of the queue for the handler.
        client (Any | None): The client for sending logs to broker, or None if not connected.
        _owns_client (bool): True when the handler built its own client and must
            close it; False when an external client was injected.
        _injected_client (Any | None): The externally-managed client, when one was
            injected; otherwise None.

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
        client: Any | None = None,
        formatter: logging.Formatter | None = None,
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
            client (Any | None): An externally-managed broker client to use instead of
                building one in ``connect()``. When provided, the handler never closes
                it — the caller owns its lifetime and recovery. Mutually exclusive with
                ``backend_config``. Defaults to None, in which case the handler connects
                and disconnects on its own.
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed from ``service_name`` and ``worker_id``.

        Attributes:
            queue_name (str): The name of the queue for the handler.
            client (Any | None): The client for sending logs to broker, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``client`` and ``backend_config`` are provided.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            backend_config=backend_config,
            formatter=formatter,
        )
        if client is not None and backend_config is not None:
            raise ValueError(
                "client and backend_config are mutually exclusive: pass an injected "
                "client OR a backend config for the handler to build its own, not both."
            )
        self.queue_name: str = queue_name
        self.client: Any | None = None
        self._owns_client: bool = client is None
        self._injected_client: Any | None = client
        if client is not None:
            self.client = client
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

    async def _connect(self) -> None:
        """Establish the broker client, honoring injected-client ownership.

        When a client was injected (``_owns_client`` is False), the handler never
        builds its own connection: it restores the durable injected reference
        (cleared by the worker's teardown) and returns. Otherwise it delegates to
        the subclass ``connect()``.
        """
        if not self._owns_client:
            self.client = self._injected_client
            return
        await self.connect()

    async def _disconnect(self) -> None:
        """Tear down the broker client, honoring injected-client ownership.

        When a client was injected, the handler never closes a connection it does
        not own — the host owns the client's lifetime and recovery — so this is a
        no-op. Otherwise it delegates to the subclass ``disconnect()``.
        """
        if not self._owns_client:
            return
        await self.disconnect()

    async def _worker(self) -> None:
        """
        Asynchronous worker to handle logging to Message broker.

        Retrieves log records from the queue, formats them, and sends them
        to the Message broker. The worker continues running
        as long as logging is active or there are records in the queue.

        Returns:
            None
        """
        connect_delay = _CONNECT_RETRY_BASE
        try:
            while (
                self.logging_running_event.is_set() or not self.log_queues[self.queue_name].empty()
            ):
                if self.client is None:
                    try:
                        await self._connect()
                    except Exception as exc:
                        self._report_error(None, exc)
                        await asyncio.sleep(
                            connect_delay
                            * random.uniform(1 - _CONNECT_RETRY_JITTER, 1 + _CONNECT_RETRY_JITTER)
                        )
                        connect_delay = min(connect_delay * 2, _CONNECT_RETRY_CAP)
                        continue
                    connect_delay = _CONNECT_RETRY_BASE
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
                name = self.worker_name
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
                        await self._disconnect()
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
                await self._disconnect()
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
