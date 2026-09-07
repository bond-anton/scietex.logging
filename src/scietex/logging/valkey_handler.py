"""Asynchronous Valkey logging handler for non-blocking logging."""

from .config import ValkeyConfig, optional_dependency_error

try:
    from glide import GlideClient, GlideClientConfiguration, NodeAddress, ServerCredentials
except ImportError as e:
    raise ImportError(optional_dependency_error("valkey-glide", "valkey"), name="glide") from e

import dataclasses
import logging
from collections.abc import Callable
from typing import cast

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
        valkey_config: dict | None = None,
        client: GlideClient | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous Valkey logging handler.

        Args:
            stream_name (str): The Valkey stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            valkey_config (dict, optional): Configuration dictionary for the Valkey connection.
                Keys mirror ``GlideClientConfiguration``'s scalar plain options; ``addresses``
                is a list of ``(host, port)`` tuples and defaults to ``[("localhost", 6379)]``.
                The dict is converted into a typed ``ValkeyConfig`` stored as
                ``self.config.backend_config``, which ``connect()`` translates into a
                ``GlideClientConfiguration``; keys left ``None`` let glide apply its own
                defaults. Defaults to ``{}``. Mutually exclusive with ``client``.
            client (Any | None): An externally-managed Valkey client to use instead of
                building one in ``connect()``. When provided, the handler never closes
                it — the caller owns its lifetime and recovery. Mutually exclusive with
                ``valkey_config``. Defaults to None.
            error_handler (callable, optional): Callback invoked with ``(record, exc)``
                when a log record cannot be delivered. Defaults to None, in which case
                errors are reported via the ``scietex.logging`` module logger.
            stdout_enable (bool): Flag to enable console logging (defaults to True).
            queue_maxsize (int): Maximum number of records each backend queue can hold.
                Defaults to 10000.
            formatter (logging.Formatter | None): Formatter used to render records
                for the console (stdout) sink only. Broker payloads are built from
                the handler's ``service_name``/``worker_id`` config and the record
                directly, so they are invariant under this formatter. Defaults to
                None, in which case a default ``ScietexFormatter`` is constructed
                from ``service_name`` and ``worker_id``.

        Attributes:
            stream_name (str): The Valkey stream name where log entries are sent.
            client (GlideClient | None): The Valkey client connection, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``client`` and ``valkey_config`` are provided.
        """
        # When a client is injected, connect() is never called so the config is
        # unused; pass None unless the caller explicitly supplied a config dict, in
        # which case it flows through so the base raises on the contradiction.
        if client is not None:
            backend_cfg = ValkeyConfig(**valkey_config) if valkey_config is not None else None
        else:
            backend_cfg = ValkeyConfig(**(valkey_config or {}))
        super().__init__(
            queue_name="valkey",
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
            formatter=formatter,
        )
        self.stream_name = stream_name

    @property
    def client_config(self) -> dict:
        """Read-only view of the backend config as a dict (backward compat)."""
        return dataclasses.asdict(cast(ValkeyConfig, self.config.backend_config))

    async def connect(self) -> None:
        """
        Connect to Valkey asynchronously.

        Builds the client from ``self.config.backend_config`` (the typed
        ``ValkeyConfig``, the single source of truth). ``addresses`` entries are
        ``(host, port)`` tuples converted to ``NodeAddress`` objects, and fields
        left ``None`` are dropped so glide applies its own defaults. A failed
        connection raises so the worker can report it and retry.

        Returns:
            None
        """
        if self.client is None:
            cfg = dataclasses.asdict(cast(ValkeyConfig, self.config.backend_config))
            addresses = cfg.pop("addresses", None)
            if not addresses:
                node_addresses = [NodeAddress()]
            else:
                node_addresses = [NodeAddress(host, port) for host, port in addresses]
            username = cfg.pop("username", None)
            password = cfg.pop("password", None)
            credentials = None
            if username is not None or password is not None:
                credentials = ServerCredentials(password=password, username=username)
            kwargs = {k: v for k, v in cfg.items() if v is not None}
            if credentials is None:
                client_config = GlideClientConfiguration(node_addresses, **kwargs)
            else:
                client_config = GlideClientConfiguration(
                    node_addresses, credentials=credentials, **kwargs
                )
            self.client = await GlideClient.create(client_config)

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

        if self.client is None:
            raise RuntimeError("Valkey client is not connected; call connect() first.")
        await self.client.xadd(self.stream_name, record.items())
