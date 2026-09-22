# Backends

`scietex.logging` ships five backends. Each is a `logging.Handler` subclass that
queues records and drains them on a background worker, so the logging call never
blocks on I/O.

```{toctree}
:maxdepth: 1

console
file
redis
valkey
mqtt
```

## Comparison

| Feature | Console | File | Redis | Valkey | MQTT |
|---------|---------|------|-------|--------|------|
| Dependencies | None | None | `redis` | `valkey-glide` | `aiomqtt` |
| Installation | Always included | Always included | `[redis]` | `[valkey]` | `[mqtt]` |
| Persistence | No | Yes | Yes | Yes | No |
| Throughput | High | High | High | High | High |
| Setup complexity | Low | Low | Medium | Medium | Medium |
| Wire format | Formatted text | Formatted text / JSON | Stream entry | Stream entry | JSON payload |
| Client injection | — | `file=` | `client=` | `client=` | `client=` |

## Which backend?

- **Console** — local development and human-readable output. Always available.
- **File** — durable local logs, with rotation and optional NDJSON output.
  Always available.
- **Redis** — centralized log aggregation over Redis streams, when your stack
  already runs Redis.
- **Valkey** — the same stream-based aggregation over Valkey (the Redis fork),
  via the `valkey-glide` client.
- **MQTT** — publishing logs to an MQTT topic for IoT / message-bus consumers.

## Using multiple backends

Handlers are fully independent — no global state — so you can attach several to
one logger:

```python
import logging

from scietex.logging import (
    AsyncFileHandler,
    AsyncMqttHandler,
    AsyncRedisHandler,
    AsyncValkeyHandler,
    ConsoleHandler,
)

logger = logging.getLogger("MultiLogger")
logger.setLevel(logging.DEBUG)

logger.addHandler(ConsoleHandler())
logger.addHandler(AsyncFileHandler("logs.log"))
logger.addHandler(AsyncRedisHandler(stream_name="logs"))
logger.addHandler(AsyncValkeyHandler(stream_name="logs"))
logger.addHandler(AsyncMqttHandler(topic="logs"))
```

Each handler must be started and stopped individually:

```python
for handler in logger.handlers:
    await handler.start_logging()
# ... log ...
for handler in logger.handlers:
    await handler.stop_logging()
```

For a runnable version that combines console, Redis, and Valkey on one logger
with explicit configs, see `examples/all_backends.py`.

## Backend architecture

All backends follow the same pattern:

1. Log records are queued via `emit()`.
2. Background workers process the queue.
3. Records are formatted and sent to the backend.
4. Graceful shutdown ensures all records are processed.

The console and file sinks are **peer backends** (`ConsoleBackend`,
`FileBackend`): each owns its queue, its worker coroutine, and its
shutdown-status reporting, and the handler registers them the same way
`AsyncBrokerHandler` registers its broker backend. A handler emits only to the
backend it registers — console output requires adding a `ConsoleHandler`
explicitly.

Broker backends (Redis, Valkey, MQTT) share the abstract `AsyncBrokerHandler`
base, which declares the `connect()` / `disconnect()` / `send_message()`
contract. See {doc}`../guide/custom-backends` to add your own.
