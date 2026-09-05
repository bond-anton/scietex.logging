# scietex.logging

**scietex.logging** is an asynchronous logging package designed for high-performance applications that require non-blocking logging. It uses `asyncio` to manage log message queues and provides multiple backends, such as console, Redis, and Valkey logging, allowing for easy extension to other logging targets.

## Features

- **Asynchronous Logging**: Log messages are queued and handled asynchronously, reducing impact on application performance.
- **Multiple Backends**: Supports console, Redis, and Valkey logging out of the box.
- **Flexible Logging Levels**: Compatible with Python's standard logging levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- **Optional Dependencies**: Only installs dependencies for the specific backends you need.

## Examples

Explore the [`examples/`](./examples) directory to see usage examples that demonstrate how to set up and work with `scietex.logging`. Each example provides a practical setup for different logging scenarios, including basic console logging and Redis-based logging.

For detailed descriptions of each example, refer to the [Examples README](./examples/README.md).

## Requirements

- Python 3.10+
- Additional dependencies for specific backends:
  - **Redis support**: `redis` (`pip install scietex.logging[redis]`)
  - **Valkey support**: `valkey-glide` (`pip install scietex.logging[valkey]`)

## Installation

Install the base package with:
```bash
pip install scietex.logging
```

To install all optional dependencies (including Redis and Valkey support), use:
```bash
pip install scietex.logging[all]
```

Or, to install individual dependencies as needed:
```bash
pip install scietex.logging[redis]   # For Redis logging
pip install scietex.logging[valkey]  # For Valkey logging
```

## Basic Usage

### Console Logging
The following example shows how to set up asynchronous console logging.

```python
import logging
from scietex.logging import AsyncBaseHandler
import asyncio

# Set up logger and handler
logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = AsyncBaseHandler()
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.info("This is an asynchronous log message")
    await handler.stop_logging()


asyncio.run(main())
```

### Redis Logging
This example demonstrates logging to a Redis stream.

```python
import logging
from scietex.logging import AsyncRedisHandler
import asyncio

# Set up logger and Redis handler
logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = AsyncRedisHandler(stream_name="my_log_stream")
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.error("This error message will be logged to Redis!")
    await handler.stop_logging()


asyncio.run(main())
```

### Valkey Logging
This example demonstrates logging to a Valkey stream.

```python
import logging
from scietex.logging import AsyncValkeyHandler
import asyncio

# Set up logger and Valkey handler
logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = AsyncValkeyHandler(stream_name="my_log_stream")
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.error("This error message will be logged to Valkey!")
    await handler.stop_logging()


asyncio.run(main())
```

## Configuration

scietex.logging is designed to allow easy configuration of additional backends and custom logging formats:

Formatting: Use Python’s standard logging Formatter to customize output. For example, to log timestamps in ISO format:

```python
formatter = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
)
handler.setFormatter(formatter)
```

## Extending scietex.logging

To add support for additional logging backends, subclass `AsyncBrokerHandler` and implement `connect()`, `disconnect()`, and `send_message()` methods. `AsyncLoggingHandler` is the pure-machinery base that owns the queue/worker infrastructure but no sink of its own; `AsyncBaseHandler` builds on it and registers the console as a peer backend (enabled by default via `stdout_enable`), while `AsyncBrokerHandler` is designed for message broker backends like Redis or Valkey.

### Example: Custom Database Handler

```python
from scietex.logging import AsyncBrokerHandler


class AsyncPostgresHandler(AsyncBrokerHandler):
    def __init__(self, db_url):
        super().__init__(queue_name="postgres")
        self.db_url = db_url
        self._db_conn = None

    async def connect(self):
        import asyncpg

        self._db_conn = await asyncpg.connect(self.db_url)

    async def disconnect(self):
        if self._db_conn:
            await self._db_conn.close()

    async def send_message(self, record):
        await self._db_conn.execute(
            "INSERT INTO logs (level, message) VALUES ($1, $2)",
            record["level"],
            record["message"],
        )
```

## Contributing

Contributions are welcome! If you find a bug or want to add a feature, please open an issue or submit a pull request.

## License

This project is licensed under the MIT License.
