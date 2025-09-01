"""Asynchronous Redis logging handler for non-blocking logging."""

from typing import Optional
from logging import LogRecord  # type: ignore

try:
    import redis.asyncio as redis
except ImportError as e:
    raise ImportError(
        "The 'redis' module is required to use this feature. "
        "Please install it by running:\n\n    pip install scietex.logging[redis]\n"
    ) from e

from .message_broker_handler import AsyncBrokerHandler


class AsyncRedisHandler(AsyncBrokerHandler):
    """
    Asynchronous Redis logging handler for non-blocking logging.

    This handler sends log records to a Redis stream, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process Redis log records queued in an asyncio queue.

    Attributes:
        stream_name (str): The Redis stream name where log entries are sent.

    Methods:
        connect_redis():
            Connect to Redis asynchronously.

        _redis_worker():
            Worker to retrieve and send log records from the queue to Redis.
    """

    def __init__(
        self,
        stream_name: str,
        service_name: Optional[str] = None,
        worker_id: Optional[int] = None,
        redis_config: Optional[dict] = None,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous Redis logging handler.

        Args:
            stream_name (str): The Redis stream name to which log records are sent.
            service_name (str, optional): Service name for log identification. Defaults to None.
            worker_id (int, optional): Identifier for the logging worker instance. Defaults to None.
            redis_config (dict, optional): Configuration dictionary for Redis connection.
                Defaults to {"host": "localhost", "port": 6379, "db": 0}.
            **kwargs: Additional keyword arguments, such as `stdout_enable`.

        Initializes the Redis logging queue and adds the Redis worker to the list of workers.
        """
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            queue_name="redis",
            **kwargs,
        )
        self.stream_name = stream_name
        self.client_config: dict = redis_config or {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }

    async def connect(self) -> None:
        """
        Connect to Redis asynchronously.

        Initializes the Redis client connection using the provided Redis configuration.
        Sets `decode_responses=True` for handling Redis data in string format.

        Returns:
            None
        """
        if self.client is None:
            self.client = await redis.Redis(**self.client_config, decode_responses=True)

    async def disconnect(self) -> None:
        """
        Disconnect from Redis asynchronously.
        """
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def send_message(self, record: LogRecord) -> None:
        """
        Send log record to Redis asynchronously.

        Args: record (LogRecord): The log record to send.

        Returns: None
        """
        if self.client is not None:
            await self.client.xadd(self.stream_name, record)  # type: ignore
