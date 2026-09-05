# Advanced Topics

This guide covers advanced usage patterns and customization options.

## Custom Backends

To create a custom logging backend, subclass `AsyncBrokerHandler` and implement the required
abstract methods. `AsyncBrokerHandler` is an abstract base class and cannot be instantiated
directly.

### Implementation Example

```python
from scietex.logging import AsyncBrokerHandler
import asyncpg


class AsyncPostgresHandler(AsyncBrokerHandler):
    def __init__(self, db_url):
        super().__init__(queue_name="postgres")
        self.db_url = db_url
        self._conn = None

    async def connect(self):
        """Connect to PostgreSQL database."""
        self._conn = await asyncpg.connect(self.db_url)

    async def disconnect(self):
        """Disconnect from PostgreSQL database."""
        if self._conn:
            await self._conn.close()

    async def send_message(self, record):
        """Send log record to PostgreSQL."""
        await self._conn.execute(
            "INSERT INTO logs (level, message, service, worker_id, timestamp) VALUES ($1, $2, $3, $4, $5)",
            record["level"],
            record["message"],
            record["name"],
            1,
            record["time"],
        )
```

### Required Methods

`AsyncBrokerHandler` declares three abstract methods that every backend must implement:

1. **`connect()`**: Establish connection to your backend
2. **`disconnect()`**: Close connection to your backend
3. **`send_message(record)`**: Send a formatted log record to your backend

A failure in `connect()` or `send_message()` must raise; the worker reports it through the
error channel and retries, so records are never silently dropped.

The record is a dictionary with the following keys:
- `level`: Log level abbreviation (DBG, INF, WRN, ERR, CRT)
- `message`: The log message
- `name`: Service and worker name
- `time`: Formatted timestamp

## Worker Configuration

### Threading Contract

`emit()` must be called from the asyncio event-loop thread. The handler captures the event
loop in `start_logging()` and raises `RuntimeError` if `emit()` is called from a different
thread. Off-loop logging is not supported.

### Timeout on Shutdown

Configure the timeout when stopping logging:

```python
await handler.stop_logging(timeout=10.0)  # 10 second timeout
```

## Error Handling

Delivery failures (queue full, connection errors, broker send errors) are reported through
the configured error channel instead of being silently dropped.

### Custom Error Handler

Pass an `error_handler` callback when constructing a handler:

```python
from scietex.logging import AsyncBaseHandler


def on_error(record, exc):
    # `record` may be None for connection-level failures.
    print(f"Logging error: {exc}")


handler = AsyncBaseHandler(error_handler=on_error)
```

When no `error_handler` is provided, errors are logged through the `scietex.logging`
module logger.

## Performance Considerations

### High Throughput

For high-throughput logging:

1. Increase the backend queue bound via `queue_maxsize` (default 10000). When a
   queue is full, records are dropped and reported through the error channel, so
   a larger bound buffers more before drops begin.
2. Use multiple workers (if implementing custom handler)

### Resource Management

Always ensure proper cleanup:

```python
async def main():
    handler = AsyncBaseHandler()
    logger.addHandler(handler)
    
    await handler.start_logging()
    # ... logging ...
    await handler.stop_logging()  # Ensure all logs are processed
```

## Extending ScietexFormatter

You can extend the formatter to add custom fields:

```python
import logging
from scietex.logging import ScietexFormatter


class CustomFormatter(ScietexFormatter):
    def format(self, record):
        # Add custom fields
        record.custom_field = "value"
        return super().format(record)
```

## Complete Custom Backend Example

```python
from scietex.logging import AsyncBrokerHandler
import aiohttp


class AsyncHTTPHandler(AsyncBrokerHandler):
    def __init__(self, url):
        super().__init__(queue_name="http")
        self.url = url
        self._session = None

    async def connect(self):
        self._session = aiohttp.ClientSession()

    async def disconnect(self):
        if self._session:
            await self._session.close()

    async def send_message(self, record):
        if self._session:
            await self._session.post(self.url, json=record)
```
