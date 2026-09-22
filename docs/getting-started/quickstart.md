# Quick Start

This page gets you from zero to asynchronous console logging in a few minutes,
then shows the same pattern for every other backend.

## The lifecycle

Every `scietex.logging` handler follows the same six-step lifecycle:

1. Create a logger and set its level.
2. Construct one or more handlers.
3. Add the handler(s) to the logger.
4. `await handler.start_logging()` to start the background worker(s).
5. Log normally (`logger.info(...)`, etc.).
6. `await handler.stop_logging()` to drain the queues and stop the workers.

`scietex.logging` is **not a replacement** for the standard `logging` module —
it extends it. Every handler subclasses `logging.Handler` (through
`AsyncLoggingHandler`), so handlers attach with `logger.addHandler(handler)` and
receive records through the normal pipeline (`logger.info(...)` → `emit()`).
Formatters subclass `logging.Formatter`. Standard levels, `logger.setLevel()`,
`logger.addHandler()`, `logger.removeHandler()`, and `logging.shutdown()` all
work unchanged, and you can mix `scietex.logging` handlers with standard-library
handlers on the same logger. The only difference is that `scietex.logging`
handlers process records asynchronously instead of synchronously in the calling
thread.

## Console logging

Console output requires adding a `ConsoleHandler` to the logger explicitly —
file and broker handlers do not register a console sink.

```python
import asyncio
import logging

from scietex.logging import ConsoleHandler

logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = ConsoleHandler()
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.info("This is an asynchronous log message")
    await handler.stop_logging()


asyncio.run(main())
```

`start_logging()` and `stop_logging()` are async and must run inside an event
loop. `emit()` is thread-safe and does **not** need a loop — it may be called
from any thread, including one with no running loop.

## File logging

File logging needs no extra dependency. Pass a filename, or swap in
`JsonFormatter` for structured output:

```python
import asyncio
import logging

from scietex.logging import AsyncFileHandler, JsonFormatter

logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = AsyncFileHandler("app.jsonl", formatter=JsonFormatter())
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.error("This error message will be written to app.jsonl as JSON!")
    await handler.stop_logging()


asyncio.run(main())
```

## Redis logging

Requires `pip install scietex.logging[redis]`.

```python
import asyncio
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
```

## Valkey logging

Requires `pip install scietex.logging[valkey]`.

```python
import asyncio
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
```

## MQTT logging

Requires `pip install scietex.logging[mqtt]`.

```python
import asyncio
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
```

## Multiple backends

Handlers are independent, so you can attach several to one logger:

```python
import logging

from scietex.logging import AsyncFileHandler, AsyncRedisHandler, ConsoleHandler

logger = logging.getLogger("MultiLogger")
logger.setLevel(logging.DEBUG)

logger.addHandler(ConsoleHandler())
logger.addHandler(AsyncFileHandler("logs.log"))
logger.addHandler(AsyncRedisHandler(stream_name="logs"))
```

Each handler must be started and stopped individually. See
{doc}`../backends/index` for a runnable multi-backend example.

## Next steps

- {doc}`../guide/lifecycle` — start/stop/restart, thread-safety, shutdown.
- {doc}`../guide/configuration` — queue bounds, error handling, typed config.
- {doc}`../guide/themes` — opt-in ANSI color.
- {doc}`../backends/index` — per-backend detail.
