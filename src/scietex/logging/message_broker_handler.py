"""Asynchronous logging handler for non-blocking logging to message broker."""

from typing import Optional, Union, Any, TypedDict
from logging import LogRecord  # type: ignore
import asyncio

from .basic_handler import AsyncBaseHandler


class BrokerConnectionConfig(TypedDict):
    """Broker server config class."""

    host: str
    port: int


class AsyncBrokerHandler(AsyncBaseHandler):
    """
    Asynchronous logging handler for non-blocking logging to message broker.

    This handler sends log records to a message broker, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process log records queued in an asyncio queue.

    Attributes:
        queue_name (str): The name of the queue for the handler.
        broker_config (dict): Configuration for the broker client connection.
        client (Any): The client for sending logs to broker.

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
        service_name: Optional[str] = None,
        worker_id: Optional[int] = None,
        broker_config: Optional[BrokerConnectionConfig] = None,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous Message broker logging handler.

        Args:
            queue_name (str): The name of the queue from which log records are read.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            broker_config (dict, optional): Configuration dictionary for message broker connection.
                Defaults to {"host": "localhost", "port": 6379}.
            **kwargs: Additional keyword arguments, such as `stdout_enable`.

        Initializes the logging queue and adds the message broker worker to the list of workers.
        """
        super().__init__(service_name=service_name, worker_id=worker_id, **kwargs)
        self.queue_name: str = queue_name
        self.broker_config: BrokerConnectionConfig = broker_config or {
            "host": "localhost",
            "port": 6379,
        }
        self.client: Optional[Any] = None
        self.log_queues[self.queue_name] = asyncio.Queue()  # Add queue for logs
        self.log_workers.append(self._worker())  # Add worker to the list

    async def connect(self) -> None:
        """
        Connect to Message broker asynchronously.

        Initializes the client connection using the provided configuration.
        Needs to be redefined in subclasses.

        Returns:
            None
        """

    async def disconnect(self) -> None:
        """
        Disconnect from Message broker asynchronously.
        Needs to be redefined in subclasses.

        Returns: None
        """
        if self.client is not None:
            self.client = None

    async def send_message(self, record: LogRecord) -> None:
        """
        Send log record to message broker asynchronously.
        Needs to be redefined in subclasses.

        Args: record (LogRecord): The log record to send.

        Returns: None
        """

    async def _worker(self) -> None:
        """
        Asynchronous worker to handle logging to Message broker.

        Retrieves log records from the queue, formats them, and sends them
        to the Message broker. The worker continues running
        as long as logging is active or there are records in the queue.

        Returns:
            None
        """
        await self.connect()  # Establish connection
        while (
            self.logging_running_event.is_set()
            or not self.log_queues[self.queue_name].empty()
        ):
            try:
                record = await asyncio.wait_for(
                    self.log_queues[self.queue_name].get(), 1
                )
                logger_name: str
                try:
                    logger_name = record.worker_name
                except AttributeError:
                    logger_name = record.name
                log_entry: dict[str, Union[str, int, float]] = {
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "name": logger_name,
                    "time": self.formatter.formatTime(record),
                }
                if self.client is not None:
                    await self.send_message(log_entry)  # type: ignore
                self.log_queues[self.queue_name].task_done()
            except asyncio.TimeoutError:
                pass
        await self.disconnect()
