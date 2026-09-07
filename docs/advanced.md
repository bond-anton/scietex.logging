# Advanced Topics

This guide covers advanced usage patterns and customization options.

## Custom Backends

To create a custom logging backend, subclass `AsyncBrokerHandler` and implement the required
abstract methods. `AsyncBrokerHandler` is an abstract base class and cannot be instantiated
directly. For a runnable in-memory backend with no external service, see
`examples/custom_backend.py`.

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

### Injecting an external client

A subclass of `AsyncBrokerHandler` (or a concrete handler) can accept an
injected `client=` keyword argument. When a client is injected, the base
machinery never closes it — the caller owns its lifetime and recovery, and
`disconnect()` is a no-op for the injected client. `client` and `backend_config`
are mutually exclusive: passing both raises `ValueError`. This lets a host
reuse an already-created, externally-managed client instead of letting the
handler build and tear down its own connection. See `examples/injected_client.py`.

A failure in `connect()` or `send_message()` must raise; the worker reports it through the
error channel. A failed `connect()` is retried (report + short sleep + retry). A failed
`send_message()` is not retried: the error is reported, the client is disconnected/closed
(so the dead connection is not reused), and the worker reconnects on the next iteration.
The failed record is dropped — it is not retried. Records are never silently dropped: a
record dropped due to a send failure is still reported through the error channel.

The record is a dictionary with the following keys:
- `level`: Log level abbreviation (DBG, INF, WRN, ERR, CRT)
- `message`: The log message
- `name`: Service and worker name
- `time`: Formatted timestamp

This record schema is **independent of the formatter**. Broker payloads are
built from the handler's `service_name`/`worker_id` config and the log record
directly, so they are invariant under `setFormatter`/`formatter=`. A custom
formatter affects the console (stdout) sink only — see
{ref}`Formatter scope: console output only <formatter-scope>`.

### Console-by-default and reserved names

`AsyncBrokerHandler` extends `AsyncBaseHandler`, so a custom broker backend
**attaches a console sink by default** (`stdout_enable=True`). Pass
`stdout_enable=False` for a broker-only handler (see `examples/custom_backend.py`).

The built-in backends reserve the queue names `"console"`, `"redis"`, and
`"valkey"`. Choose a distinct `queue_name` for your custom backend — using a
reserved name raises `ValueError` at construction when the console backend is
enabled.

## Worker Configuration

### Threading Contract

`emit()` is **thread-safe** and may be called from any thread, including a
thread with no running asyncio loop. The handler captures the event loop in
`start_logging()` for its bridge task; `emit()` itself writes the record to a
bounded, thread-safe stdlib `queue.Queue` ingress, and a bridge task on the
loop thread re-dispatches it into the per-backend queues. An off-loop `emit()`
therefore **delivers** the record instead of dropping it.

### Timeout on Shutdown

Configure the timeout when stopping logging:

```python
await handler.stop_logging(timeout=10.0)  # 10 second timeout
```

`examples/restartable_lifecycle.py` demonstrates the full lifecycle, including
`stop_logging(timeout)`, idempotent stop, and the `RuntimeError` raised by a
double start.

Since 1.6.0 the Console and File backends write on a background single-thread
executor, and their teardown uses a `shutdown(wait=True)` contract: even if
`stop_logging(timeout)` returns a `TIMEOUT` from the queue drain, it still waits
for any in-flight `write` to finish before closing the stream — guaranteeing
**no write-after-close**. This is a deliberate correctness-over-latency choice
(the wait is not bounded by the timeout, because bounding it would reintroduce
the write-after-close race). See `docs/configuration.md`.

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

Since 1.6.0 the blocking `write`/`flush` (and rollover/reopen) of the Console
and File backends runs on a dedicated single-thread executor per backend, so a
slow sink no longer stalls the event loop — formatting stays on the loop, only
the blocking I/O moves off it.

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
