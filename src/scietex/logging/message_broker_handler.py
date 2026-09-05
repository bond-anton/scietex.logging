"""Asynchronous logging handler for non-blocking logging to message broker."""

import abc
import asyncio
from datetime import datetime, timezone
from typing import Any

from .async_logging_handler import BackendDrainResult, DrainStatus
from .basic_handler import AsyncBaseHandler
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
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous Message broker logging handler.

        Args:
            queue_name (str): The name of the queue from which log records are read.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            **kwargs: Additional keyword arguments, such as `stdout_enable`.

        Attributes:
            queue_name (str): The name of the queue for the handler.
            client (Any | None): The client for sending logs to broker, or None if not connected.
        """
        super().__init__(service_name=service_name, worker_id=worker_id, **kwargs)
        self.queue_name: str = queue_name
        self.client: Any | None = None
        self.register_backend(
            self.queue_name, asyncio.Queue(maxsize=self.queue_maxsize), self._worker, self.drain
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

        Subclasses must deliver the record to the broker. A failure must raise so the
        worker can report it without acknowledging the queue task.

        Args:
            record (dict[str, str]): The log record to send as a dictionary.

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
        while self.logging_running_event.is_set() or not self.log_queues[self.queue_name].empty():
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
            # Compute the broker fields directly instead of reading formatter-mutated
            # record attributes, keeping output deterministic regardless of stdout_enable.
            level = level_abbreviation(record.levelno)
            name = getattr(self.formatter, "worker_name", record.name)
            log_entry: dict[str, str] = {
                "level": level,
                "message": record.getMessage(),
                "name": name,
                "time": self.formatter.formatTime(record)
                if self.formatter
                else datetime.now(timezone.utc).isoformat(),
            }
            try:
                await self.send_message(log_entry)
            except Exception as exc:
                self._report_error(record, exc)
                continue
            self.log_queues[self.queue_name].task_done()
        try:
            await self.disconnect()
        except Exception as exc:
            self._report_error(None, exc)

    async def drain(self, timeout: float, results: list[BackendDrainResult]) -> None:
        """
        Drain the broker queue and record the outcome for status reporting.

        Waits for every queued record to be acknowledged by the worker, then appends
        a result describing how the drain concluded so a status-reporting backend
        (e.g. the console) can surface it during shutdown.

        Args:
            timeout (float): Timeout for the queue to drain.
            results (list[BackendDrainResult]): Shared list to which the outcome is
                appended.

        Returns:
            None
        """
        try:
            await asyncio.wait_for(self.log_queues[self.queue_name].join(), timeout=timeout)
        except asyncio.TimeoutError:
            results.append(BackendDrainResult(self.queue_name, DrainStatus.TIMEOUT))
        except Exception as exc:
            results.append(BackendDrainResult(self.queue_name, DrainStatus.ERROR, exc))
        else:
            results.append(BackendDrainResult(self.queue_name, DrainStatus.COMPLETED))
