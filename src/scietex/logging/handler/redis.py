"""Asynchronous Redis logging handler for non-blocking logging."""

from ..config import RedisConfig, optional_dependency_error

try:
    import redis.asyncio as redis
except ImportError as e:
    raise ImportError(optional_dependency_error("redis", "redis"), name="redis") from e

import logging
from collections.abc import Callable

from ..async_logging_handler import _QUEUE_REDIS
from .broker import AsyncBrokerHandler


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
        *,
        redis_config: dict | None = None,
        client: redis.Redis | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
    ) -> None:
        """
        Initialize the asynchronous Redis logging handler.

        Args:
            stream_name (str): The Redis stream name to which log records are sent.
            redis_config (dict, optional): Configuration dictionary for Redis connection.
                Defaults to {"host": "localhost", "port": 6379, "db": 0}. Keys are
                converted into a typed ``RedisConfig`` stored as
                ``self.backend_config``, which is the single source passed to
                ``redis.Redis`` by ``connect()``. Mutually exclusive with ``client``.
            client (redis.Redis | None): An externally-managed Redis client to use
                instead of building one in ``connect()``. When provided, the handler
                never closes it — the caller owns its lifetime and recovery. Mutually
                exclusive with ``redis_config``. Defaults to None.
            error_handler (callable, optional): Callback invoked with ``(record, exc)``
                when a log record cannot be delivered. Defaults to None, in which case
                errors are reported via the ``scietex.logging`` module logger.
            queue_maxsize (int): Maximum number of records each backend queue can hold.
                Defaults to 10000.

        Attributes:
            stream_name (str): The Redis stream name where log entries are sent.
            client (redis.Redis | None): The Redis client connection, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``client`` and ``redis_config`` are provided.
        """
        # When a client is injected, connect() is never called so the config is
        # unused; pass None unless the caller explicitly supplied a config dict, in
        # which case it flows through so the base raises on the contradiction.
        if client is not None:
            backend_cfg = RedisConfig(**redis_config) if redis_config is not None else None
        else:
            backend_cfg = RedisConfig(
                **(redis_config or {"host": "localhost", "port": 6379, "db": 0})
            )
        super().__init__(
            queue_name=_QUEUE_REDIS,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
        )
        self.stream_name = stream_name

    async def connect(self) -> None:
        """
        Connect to Redis asynchronously.

        Builds the client from ``self.backend_config`` (the typed
        ``RedisConfig``, the single source of truth), honoring the user's
        ``decode_responses`` value rather than forcing it True. A ping probes
        connectivity before the client is considered connected.

        Returns:
            None
        """
        if self.client is None:
            cfg = self._config_dict()
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
