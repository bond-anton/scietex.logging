"""Asynchronous Valkey logging handler for non-blocking logging."""

try:
    from glide import GlideClient, GlideClientConfiguration, NodeAddress
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
    separate worker to process Valkey log records queued in an asyncio queue.

    Attributes:
        stream_name (str): The Valkey stream name where log entries are sent.
        client (GlideClient | None): The Valkey client connection, or None if not connected.

    Methods:
        connect():
            Connect to Valkey asynchronously.
        disconnect():
            Disconnect from Valkey asynchronously.
        send_message():
            Send log record to Valkey asynchronously.
    """

    def __init__(
        self,
        stream_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        valkey_config: GlideClientConfiguration | None = None,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous Valkey logging handler.

        Args:
            stream_name (str): The Valkey stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            valkey_config (GlideClientConfiguration, optional): Configuration for Valkey connection.
                Defaults to a basic configuration with default host and port.
            **kwargs: Additional keyword arguments, such as `stdout_enable`.

        Attributes:
            stream_name (str): The Valkey stream name where log entries are sent.
            client (GlideClient | None): The Valkey client connection, or None if not connected.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            queue_name="valkey",
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
        A failed connection raises so the worker can report it and retry.

        Returns:
            None
        """
        if self.client is None:
            self.client = await GlideClient.create(self.client_config)

    async def disconnect(self) -> None:
        """
        Disconnect Valkey asynchronously.
        """
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        """
        Send log record to Valkey asynchronously.

        Args:
            record (dict[str, str]): The log record to send as a dictionary.

        Returns:
            None
        """

        if self.client is not None:
            await self.client.xadd(self.stream_name, record.items())
