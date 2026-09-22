# Custom Backends

To create a custom logging backend, subclass `AsyncBrokerHandler` and implement
the required abstract methods. `AsyncBrokerHandler` is an abstract base class and
cannot be instantiated directly. For a runnable in-memory backend with no
external service, see `examples/custom_backend.py`.

## Required methods

`AsyncBrokerHandler` declares three abstract methods that every backend must
implement:

1. **`connect()`**: Establish connection to your backend.
2. **`disconnect()`**: Close connection to your backend.
3. **`send_message(record)`**: Send a log record to your backend.

## Implementation example

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
            "INSERT INTO logs (level, message, name, timestamp) VALUES ($1, $2, $3, $4)",
            record["level"],
            record["message"],
            record["name"],
            record["time"],
        )
```

## The record schema

The record passed to `send_message` is a dictionary with the following keys:

- `level`: Log level abbreviation (DBG, INF, WRN, ERR, CRT)
- `message`: The log message
- `name`: The record's logger name (`record.name`)
- `time`: Formatted timestamp

This record schema is **independent of the formatter**. Broker payloads are
built from the log record directly — the `name` field is the record's logger
name (`record.name`) — so they are invariant under `setFormatter`. Broker
handlers do not accept a `formatter=` keyword (passing one raises `TypeError`);
a formatter affects the console (stdout) and file sinks only — see
{ref}`Formatter scope: console and file output only <formatter-scope>`.

## Failure semantics

A failure in `connect()` or `send_message()` must raise; the worker reports it
through the error channel. A failed `connect()` is retried (report + short sleep
+ retry). A failed `send_message()` is not retried: the error is reported, the
client is disconnected/closed (so the dead connection is not reused), and the
worker reconnects on the next iteration. The failed record is dropped — it is
not retried. Records are never silently dropped: a record dropped due to a send
failure is still reported through the error channel.

## Injecting an external client

A subclass of `AsyncBrokerHandler` (or a concrete handler) can accept an
injected `client=` keyword argument. When a client is injected, the base
machinery never closes it — the caller owns its lifetime and recovery, and
`disconnect()` is a no-op for the injected client. `client` and the backend
config are mutually exclusive: passing both raises `ValueError`. This lets a
host reuse an already-created, externally-managed client instead of letting the
handler build and tear down its own connection. See
`examples/injected_client.py`.

## Reserved names

`AsyncBrokerHandler` extends `AsyncLoggingHandler` directly, so a custom broker
backend registers **only its own broker backend** — no console sink is attached.
To add console output alongside a broker handler, add a `ConsoleHandler` to the
logger separately.

The built-in backends register under `_`-prefixed queue keys:

- `"_console"` — registered by `ConsoleHandler` (always).
- `"_file"` — registered by `AsyncFileHandler` and its rotation variants.
- `"_redis"` / `"_valkey"` — registered by `AsyncRedisHandler` /
  `AsyncValkeyHandler`.
- `"_mqtt"` — registered by `AsyncMqttHandler`.

The `_` prefix is reserved for internal use — a user-supplied `queue_name` never
starts with `_`, so it cannot collide with a built-in backend. `register_backend`
still raises `ValueError` at construction if two custom backends reuse the same
`queue_name`.

For shutdown status text, the built-in peer backends (console and file) pass an
explicit display name (e.g. "Console", "File") rather than deriving it from the
queue key; the `_`-strip is only a fallback applied when a drain result carries
no explicit display name (broker handlers and custom backends), e.g. "Console
Logger has completed processing its queue.".

## Pure-machinery handlers

For a handler with **no console sink at all**, subclass the pure-machinery base
`AsyncLoggingHandler` directly. It owns the shared queue/worker/event machinery
but registers no backend of its own, so you add only the backends you want. See
`examples/pure_machinery_handler.py` for a runnable version:

```python
import asyncio

from scietex.logging import AsyncLoggingHandler


class MyHandler(AsyncLoggingHandler):
    def __init__(
        self,
        *,
        error_handler=None,
        queue_maxsize=10000,
    ):
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
        # register your own backend(s) via self.register_backend(...)
        # worker is a zero-arg factory returning a fresh coroutine per start:
        self.register_backend("my_backend", asyncio.Queue(), self._worker, self.drain)

    async def _worker(self):
        # drains self.log_queues["my_backend"]; called fresh on each start_logging
        ...

    async def drain(self, timeout): ...
```

## Complete custom backend example

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
