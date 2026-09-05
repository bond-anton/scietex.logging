"""Asynchronous Redis logging handler for non-blocking logging."""

from .config import RedisConfig, optional_dependency_error

try:
    import redis.asyncio as redis
except ImportError as e:
    raise ImportError(optional_dependency_error("redis", "redis"), name="redis") from e

import dataclasses
import logging
from collections.abc import Callable
from typing import cast

from .message_broker_handler import AsyncBrokerHandler


class AsyncRedisHandler(AsyncBrokerHandler):
    """
    Asynchronous Redis logging handler for non-blocking logging.

    This handler sends log records to a Redis stream, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process Redis log records queued in an asyncio queue.

    Attributes:
        stream_name (str): The Redis stream name where log entries are sent.
        client (redis.Redis | None): The Redis client connection, or None if not connected.

    Methods:
        connect():
            Connect to Redis asynchronously.
        disconnect():
            Disconnect from Redis asynchronously.
        send_message():
            Send log record to Redis asynchronously.
    """

    def __init__(
        self,
        stream_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        redis_config: dict | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        """
        Initialize the asynchronous Redis logging handler.

        Args:
            stream_name (str): The Redis stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            redis_config (dict, optional): Configuration dictionary for Redis connection.
                Defaults to {"host": "localhost", "port": 6379, "db": 0}. Keys are
                converted into a typed ``RedisConfig`` stored as
                ``self.config.backend_config``, which is the single source passed to
                ``redis.Redis`` by ``connect()``.
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
            stream_name (str): The Redis stream name where log entries are sent.
            client (redis.Redis | None): The Redis client connection, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
        """
        raw = redis_config or {"host": "localhost", "port": 6379, "db": 0}
        super().__init__(
            queue_name="redis",
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
            backend_config=RedisConfig(**raw),
            formatter=formatter,
        )
        self.stream_name = stream_name

    @property
    def client_config(self) -> dict:
        """Read-only view of the backend config as a dict (backward compat)."""
        return dataclasses.asdict(cast(RedisConfig, self.config.backend_config))

    async def connect(self) -> None:
        """
        Connect to Redis asynchronously.

        Builds the client from ``self.config.backend_config`` (the typed
        ``RedisConfig``, the single source of truth), honoring the user's
        ``decode_responses`` value rather than forcing it True. A ping probes
        connectivity before the client is considered connected.

        Returns:
            None
        """
        if self.client is None:
            cfg = dataclasses.asdict(cast(RedisConfig, self.config.backend_config))
            client = await redis.Redis(**cfg)
            try:
                await client.ping()
            except Exception:
                await client.aclose()
                raise
            self.client = client

    async def disconnect(self) -> None:
        """
        Disconnect from Redis asynchronously.
        """
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        """
        Send log record to Redis asynchronously.

        Args:
            record (dict[str, str]): The log record to send as a dictionary.

        Returns:
            None
        """
        if self.client is None:
            raise RuntimeError("Redis client is not connected; call connect() first.")
        await self.client.xadd(self.stream_name, record)
