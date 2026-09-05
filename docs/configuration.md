# Configuration

This guide covers configuring scietex.logging, including formatters, service names, and custom formats.

## ScietexFormatter

The `ScietexFormatter` is the default formatter for scietex.logging. It provides:

- Service name and worker ID in logs: `{service_name}:{worker_id}`
- 3-letter log level abbreviations: `DBG`, `INF`, `WRN`, `ERR`, `CRT`
- ISO 8601 UTC timestamps by default

### Basic Usage

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter(service_name="MyService", worker_id=1)
```

### Custom Format

You can customize the log format by passing a custom `fmt` string:

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter(
    service_name="MyService",
    worker_id=1,
    fmt="%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

### Date Format

By default, timestamps use ISO 8601 format with UTC timezone. You can customize this:

```python
from scietex.logging import ScietexFormatter
from datetime import datetime, timezone

formatter = ScietexFormatter(service_name="MyService", worker_id=1, datefmt="%Y-%m-%d %H:%M:%S")
```

## Handler Configuration

### Service Name and Worker ID

Both `AsyncBaseHandler` and `AsyncBrokerHandler` accept `service_name` and `worker_id` parameters:

```python
handler = AsyncBaseHandler(service_name="MyService", worker_id=1)
```

### Console Logging Control

Console logging is a **peer backend** (`ConsoleBackend`) that `AsyncBaseHandler`
registers by default. It can be disabled by setting `stdout_enable=False`:

```python
handler = AsyncBaseHandler(stdout_enable=False)
```

For a handler with **no console sink at all**, subclass the pure-machinery base
`AsyncLoggingHandler` directly instead of `AsyncBaseHandler`. It owns the shared
queue/worker/event machinery but registers no backend of its own, so you add
only the backends you want:

```python
import asyncio

from scietex.logging import AsyncLoggingHandler


class MyHandler(AsyncLoggingHandler):
    def __init__(
        self,
        service_name=None,
        worker_id=None,
        *,
        error_handler=None,
        queue_maxsize=10000,
    ):
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
        # register your own backend(s) via self.register_backend(...)
        # worker is a zero-arg factory returning a fresh coroutine per start:
        self.register_backend("my_backend", asyncio.Queue(), self._worker, self.drain)

    async def _worker(self):
        # drains self.log_queues["my_backend"]; called fresh on each start_logging
        ...

    async def drain(self, timeout, results):
        ...
```

### Error Handler

Provide an `error_handler` callback to receive delivery failures (queue full, connection
errors, broker send errors):

```python
def on_error(record, exc):
    print(f"Logging error: {exc}")


handler = AsyncBaseHandler(error_handler=on_error)
```

### Queue Bounds and Overflow

Each backend queue is **bounded** by `queue_maxsize`, a keyword-only constructor
parameter on `AsyncLoggingHandler` and `AsyncBaseHandler` (default `10000`). It
is validated to a positive int via `validate_queue_maxsize` (invalid values
raise `ValueError`), stored as `self.queue_maxsize`, and applied to every
backend queue the handler registers — the console queue and, for broker
handlers, the broker queue.

```python
handler = AsyncBaseHandler(queue_maxsize=5000)
```

The overflow policy is **drop + report**. When a backend queue is full at emit
time, `emit` drops the record and routes an `asyncio.QueueFull` to the error
channel (`error_handler` callback, or the `scietex.logging` module logger when
none is configured). `emit` never blocks and never buffers unboundedly, so the
producer stays non-blocking under sustained overload. Under such overload,
records are dropped and reported rather than buffered without limit.

### Typed Configuration

Every handler builds a frozen dataclass `self.config` from its explicit
constructor keyword arguments (defined in `src/scietex/logging/config.py`):

- `LoggingConfig` — shared machinery options for every handler:
  `service_name`, `worker_id`, `error_handler`, `queue_maxsize`,
  `stdout_enable`, and `backend_config` (the backend-specific config, or `None`
  for the pure-machinery/console-only handlers).
- `RedisConfig` — Redis connection settings (`host`, `port`, `db`). Stored as
  `self.config.backend_config` on `AsyncRedisHandler`.
- `ValkeyConfig` — Valkey node addresses (a list of `(host, port)` tuples).
  Stored as `self.config.backend_config` on `AsyncValkeyHandler`.

The handler constructors **no longer accept `**kwargs`**. Unknown or typo'd
keyword arguments now raise `TypeError` at construction time instead of being
silently swallowed. `AsyncRedisHandler` converts its `redis_config` dict into a
typed `RedisConfig`; unknown keys in that dict also raise `TypeError`.

### Threading Contract

`emit()` must be called from the asyncio event-loop thread. The handler raises
`RuntimeError` if `emit()` is called off-loop.

## Custom Formatters

You can use Python's standard `logging.Formatter` with custom formats:

```python
import logging
from scietex.logging import AsyncBaseHandler

formatter = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
)

handler = AsyncBaseHandler()
handler.setFormatter(formatter)
```

## Complete Example

```python
import logging
from scietex.logging import AsyncBaseHandler, ScietexFormatter

logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)

formatter = ScietexFormatter(
    service_name="MyService",
    worker_id=1,
    fmt="%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s",
)

handler = AsyncBaseHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)
```
