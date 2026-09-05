"""Asynchronous Valkey logging handler for non-blocking logging."""

from .config import ValkeyConfig, optional_dependency_error

try:
    from glide import GlideClient, GlideClientConfiguration, NodeAddress
except ImportError as e:
    raise ImportError(optional_dependency_error("valkey-glide", "valkey")) from e

import logging
from collections.abc import Callable

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
        *,
        valkey_config: GlideClientConfiguration | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
    ) -> None:
        """
        Initialize the asynchronous Valkey logging handler.

        Args:
            stream_name (str): The Valkey stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            valkey_config (GlideClientConfiguration, optional): Configuration for Valkey connection.
                Defaults to a basic configuration with default host and port.
            error_handler (callable, optional): Callback invoked with ``(record, exc)``
                when a log record cannot be delivered. Defaults to None, in which case
                errors are reported via the ``scietex.logging`` module logger.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
                Defaults to 10000.

        Attributes:
            stream_name (str): The Valkey stream name where log entries are sent.
            client (GlideClient | None): The Valkey client connection, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        client_config = (
            valkey_config
            if valkey_config is not None
            else GlideClientConfiguration([NodeAddress()])
        )
        super().__init__(
            queue_name="valkey",
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            backend_config=ValkeyConfig(
                addresses=[(node.host, node.port) for node in client_config.addresses]
            ),
        )
        self.stream_name = stream_name
        self.client_config: GlideClientConfiguration = client_config

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
