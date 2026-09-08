"""
scietex.logging: An Asynchronous Logging Package

This package provides a flexible framework for asynchronous logging, supporting
multiple logging backends, such as the console, Redis, Valkey, and MQTT. It leverages
`asyncio` to allow non-blocking logging, ideal for applications requiring
high-performance logging without impacting main application tasks.

Features:
---------
- **Asynchronous Logging**: Log messages are queued and handled asynchronously, ensuring minimal
  interference with the main application flow.
- **Multiple Backends**: Console, File, Redis, Valkey, and MQTT logging are supported
  out of the box, with an option to extend to other backends.
- **File Logging**: Plain-text file logging with rotation variants and structured JSON output
  are available out of the box with no extra dependencies.
- **Configurable Logging Levels**: Supports all standard logging levels
  (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL).
- **Optional Dependency Management**: Only install the necessary dependencies for the backends
  you intend to use.

Dependencies:
-------------
This package requires Python 3.10+ and the standard `logging` library.
Additional dependencies are required for certain backends:
- **Redis support**: Install with `pip install scietex.logging[redis]` to enable Redis logging.
- **Valkey support**: Install with `pip install scietex.logging[valkey]` to enable Valkey logging.
- **MQTT support**: Install with `pip install scietex.logging[mqtt]` to enable MQTT logging.

Installation:
-------------
Install the base package with:
    pip install scietex.logging

To install optional dependencies for specific backends, use the extras syntax:
    pip install scietex.logging[redis]   # To enable Redis backend
    pip install scietex.logging[valkey]  # To enable Valkey backend
    pip install scietex.logging[mqtt]    # To enable MQTT backend
    pip install scietex.logging[all]     # To enable all backends

Example Usage:
--------------
Basic usage with console logging:

    import logging
    from scietex.logging import ConsoleHandler

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = ConsoleHandler()
    logger.addHandler(handler)

    async def main():
        await handler.start_logging()  # Start the logging worker
        logger.info("This is an asynchronous log message")
        await handler.stop_logging()   # Stop the worker and flush remaining logs

    asyncio.run(main())

Advanced usage with Redis logging:

    import logging
    from scietex.logging import AsyncRedisHandler

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = AsyncRedisHandler(stream_name="my_log_stream")
    logger.addHandler(handler)

    async def main():
        await handler.start_logging()
        logger.error("This error message will be logged to Redis!")
        await handler.stop_logging()

    asyncio.run(main())

Advanced usage with Valkey logging:

    import logging
    from scietex.logging import AsyncValkeyHandler

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = AsyncValkeyHandler(stream_name="my_log_stream")
    logger.addHandler(handler)

    async def main():
        await handler.start_logging()
        logger.error("This error message will be logged to Valkey!")
        await handler.stop_logging()

    asyncio.run(main())

Advanced usage with MQTT logging:

    import logging
    from scietex.logging import AsyncMqttHandler

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = AsyncMqttHandler(topic="my/log/topic")
    logger.addHandler(handler)

    async def main():
        await handler.start_logging()
        logger.error("This error message will be logged to MQTT!")
        await handler.stop_logging()

    asyncio.run(main())

Advanced usage with file logging:

    import logging
    from scietex.logging import AsyncFileHandler, JsonFormatter

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = AsyncFileHandler("app.log", formatter=JsonFormatter())
    logger.addHandler(handler)

    async def main():
        await handler.start_logging()
        logger.error("This error message will be written to app.log as JSON!")
        await handler.stop_logging()

    asyncio.run(main())

Extending the Package:
----------------------
Custom backends can be implemented by subclassing `AsyncBrokerHandler` and implementing the
`connect()`, `disconnect()`, and `send_message()` methods.

See the package documentation for more details on extending and configuring custom logging
behaviors.

"""

__version__ = "2.0.0"

from .async_logging_handler import AsyncLoggingHandler
from .backend import ConsoleBackend, FileBackend
from .config import LoggingConfig, MqttConfig, RedisConfig, ValkeyConfig
from .formatter import JsonFormatter, ScietexFormatter
from .handler import (
    AsyncBrokerHandler,
    AsyncFileHandler,
    AsyncRotatingFileHandler,
    AsyncTimedRotatingFileHandler,
    AsyncWatchedFileHandler,
    ConsoleHandler,
)

__all__ = [
    "AsyncBrokerHandler",
    "AsyncFileHandler",
    "AsyncLoggingHandler",
    "AsyncRotatingFileHandler",
    "AsyncTimedRotatingFileHandler",
    "AsyncWatchedFileHandler",
    "ConsoleBackend",
    "ConsoleHandler",
    "FileBackend",
    "JsonFormatter",
    "LoggingConfig",
    "MqttConfig",
    "RedisConfig",
    "ScietexFormatter",
    "ValkeyConfig",
]

try:
    from .handler.redis import AsyncRedisHandler

    __all__ += ["AsyncRedisHandler"]
except ImportError as exc:
    # Only swallow the missing-optional-client failure; surface any other
    # ImportError (e.g. a bug in the backend module or a missing transitive dep).
    if exc.name != "redis":
        raise
try:
    from .handler.valkey import AsyncValkeyHandler

    __all__ += ["AsyncValkeyHandler"]
except ImportError as exc:
    if exc.name != "glide":
        raise
try:
    from .handler.mqtt import AsyncMqttHandler

    __all__ += ["AsyncMqttHandler"]
except ImportError as exc:
    if exc.name != "aiomqtt":
        raise
