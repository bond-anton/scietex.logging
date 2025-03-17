"""Asynchronous Redis logging handler for non-blocking logging."""

from typing import Union, TypedDict
import asyncio

try:
    import redis.asyncio as redis
except ImportError as e:
    raise ImportError(
        "The 'redis' module is required to use this feature. "
        "Please install it by running:\n\n    pip install mypackage[redis]\n"
    ) from e

from .basic_handler import AsyncBaseHandler


class RedisConfig(TypedDict):
    """Redis config class."""

    host: str
    port: int
    db: int


class AsyncRedisHandler(AsyncBaseHandler):
    """
    Asynchronous Redis logging handler for non-blocking logging.

    This handler sends log records to a Redis stream, enabling asynchronous
    logging without blocking the main application. The handler maintains a
    separate worker to process Redis log records queued in an asyncio queue.

    Attributes:
        stream_name (str): The Redis stream name where log entries are sent.
        redis_config (dict): Configuration for the Redis client connection.
        redis_client (redis.client.Redis): Redis client for sending logs.

    Methods:
        connect_redis():
            Connect to Redis asynchronously.

        _redis_worker():
            Worker to retrieve and send log records from the queue to Redis.
    """

    def __init__(
        self,
        stream_name: str,
        service_name: Union[str, None] = None,
        worker_id: Union[int, None] = None,
        redis_config: Union[RedisConfig, None] = None,
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
        super().__init__(service_name=service_name, worker_id=worker_id, **kwargs)
        self.stream_name = stream_name
        self.redis_config: RedisConfig = redis_config or {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }
        self.redis_client: Union[redis.client.Redis, None] = None
        self.log_queues["redis"] = asyncio.Queue()  # Add queue for Redis logs
        self.log_workers.append(self._redis_worker())  # Add Redis worker to the list

    async def connect_redis(self) -> None:
        """
        Connect to Redis asynchronously.

        Initializes the Redis client connection using the provided Redis configuration.
        Sets `decode_responses=True` for handling Redis data in string format.

        Returns:
            None
        """
        self.redis_client = await redis.Redis(
            **self.redis_config, decode_responses=True
        )

    async def _redis_worker(self) -> None:
        """
        Asynchronous worker to handle logging to Redis.

        Retrieves log records from the Redis queue, formats them, and sends them
        to the Redis stream specified by `stream_name`. The worker continues running
        as long as logging is active or there are records in the queue.

        Returns:
            None
        """
        await self.connect_redis()  # Establish Redis connection in the worker
        while (
            self.logging_running_event.is_set() or not self.log_queues["redis"].empty()
        ):
            try:
                record = await asyncio.wait_for(self.log_queues["redis"].get(), 1)
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
                if self.redis_client is not None:
                    await self.redis_client.xadd(self.stream_name, log_entry)  # type: ignore
                self.log_queues["redis"].task_done()
            except asyncio.TimeoutError:
                pass
