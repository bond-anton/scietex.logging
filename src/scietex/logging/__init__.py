"""
scietex.logging: An Asynchronous Logging Package

This package provides a flexible framework for asynchronous logging, supporting multiple logging
backends, such as the console and Redis. It leverages `asyncio` to allow non-blocking logging,
ideal for applications requiring high-performance logging without impacting main application tasks.

Features:
---------
- **Asynchronous Logging**: Log messages are queued and handled asynchronously, ensuring minimal
  interference with the main application flow.
- **Multiple Backends**: Console and Redis logging are supported out of the box, with an option
  to extend to other backends like PostgreSQL or other databases.
- **Configurable Logging Levels**: Supports all standard logging levels
  (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL).
- **Optional Dependency Management**: Only install the necessary dependencies for the backends
  you intend to use.

Dependencies:
-------------
This package requires Python 3.7+ and the standard `logging` library.
Additional dependencies are required for certain backends:
- **Redis support**: Install with `pip install scietex.logging[redis]` to enable Redis logging.
- **PostgreSQL support**: *Coming Soon!* Install with `pip install scietex.logging[postgres]`
  to enable PostgreSQL logging.

Installation:
-------------
Install the base package with:
    pip install scietex.logging

To install optional dependencies for specific backends, use the extras syntax:
    pip install scietex.logging[redis]     # To enable Redis backend
    pip install scietex.logging[postgres]  # To enable PostgreSQL backend
    pip install scietex.logging[all]       # To enable all backends

Example Usage:
--------------
Basic usage with console logging:

    import logging
    from scietex.logging import AsyncBaseHandler

    logger = logging.getLogger("MyAsyncLogger")
    logger.setLevel(logging.DEBUG)
    handler = AsyncBaseHandler()
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

Extending the Package:
----------------------
Custom backends can be implemented by subclassing `AsyncBaseHandler` and adding new worker methods
for additional queues.

See the package documentation for more details on extending and configuring custom logging
behaviors.

"""

__version__ = "0.1.1"

from .formatter import NTSFormatter
from .basic_handler import AsyncBaseHandler
from .message_broker_handler import AsyncBrokerHandler

try:
    from .redis_handler import AsyncRedisHandler
except ImportError:
    pass
try:
    from .valkey_handler import AsyncValkeyHandler
except ImportError:
    pass
