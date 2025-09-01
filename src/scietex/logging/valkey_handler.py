"""Asynchronous Valkey logging handler for non-blocking logging."""

from typing import Optional
from logging import LogRecord  # type: ignore

try:
    from glide import GlideClient, GlideClientConfiguration, NodeAddress, ClosingError
except ImportError as e:
    raise ImportError(
        "The 'valkey-glide' module is required to use this feature. "
        "Please install it by running:\n\n    pip install scietex.logging[valkey]\n"
    ) from e

from .message_broker_handler import AsyncBrokerHandler


class AsyncValkeyHandler(AsyncBrokerHandler):
    """
    Asynchronous Valkey logging handler for non-blocking logging.

    This handler sends log records to a Valkey stream, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process Redis log records queued in an asyncio queue.

    Attributes:
        stream_name (str): The Valkey stream name where log entries are sent.

    Methods:
        connect_valkey():
            Connect to Valkey asynchronously.

        _valkey_worker():
            Worker to retrieve and send log records from the queue to Valkey.
    """

    def __init__(
        self,
        stream_name: str,
        service_name: Optional[str] = None,
        worker_id: Optional[int] = None,
        valkey_config: Optional[GlideClientConfiguration] = None,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous Valkey logging handler.

        Args:
            stream_name (str): The Valkey stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            valkey_config (dict, optional): Configuration dictionary for Valkey connection.
                Defaults to {"host": "localhost", "port": 6379, "db": 0}.
            **kwargs: Additional keyword arguments, such as `stdout_enable`.

        Initializes the Valkey logging queue and adds the Valkey worker to the list of workers.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            queue_name="valkey",
            broker_config=None,
            **kwargs,
        )
        self.stream_name = stream_name
        self.client_config: GlideClientConfiguration
        if valkey_config is not None:
            self.client_config = valkey_config
        else:
            self.client_config = GlideClientConfiguration([NodeAddress()])

    async def connect(self) -> None:
        """
        Connect to Valkey asynchronously.

        Initializes the Valkey client connection using the provided Valkey configuration.
        Sets `decode_responses=True` for handling Valkey data in string format.

        Returns:
            None
        """
        if self.client is None:
            try:
                self.client = await GlideClient.create(self.client_config)
            except ClosingError:
                pass

    async def disconnect(self) -> None:
        """
        Disconnect Valkey asynchronously.
        """
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def send_message(self, record: LogRecord) -> None:
        """
        Send log record to Valkey asynchronously.

        Args: record (LogRecord): The log record to send.

        Returns: None
        """
        if self.client is not None:
            await self.client.xadd(self.stream_name, record.items())  # type: ignore
