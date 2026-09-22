# Configuration

This guide covers the shared machinery options every handler accepts: logger
identity, queue bounds, error handling, and typed configuration.

## Logger name as identity

Identity comes solely from the standard-library logger name. The name you pass
to `logging.getLogger("name")` becomes `record.name`, which is rendered via
`%(name)s` in the default format and emitted as the `name`/`logger` field by
broker/JSON sinks. There is no separate identity field to configure — name your
loggers and the identity follows.

```python
logger = logging.getLogger("MyService")  # record.name == "MyService"
handler = ConsoleHandler()
```

Note the edge case: the root logger (`logging.getLogger()` with no argument) has
an **empty** `record.name`, so its log lines carry an empty identity field.
Always name your loggers to get useful identification.

## Shared machinery options

Every handler accepts these keyword-only options:

| Option | Type | Default | Meaning |
|---|---|---|---|
| `error_handler` | `Callable[[LogRecord \| None, Exception], None] \| None` | `None` | Delivery-error callback. |
| `queue_maxsize` | `int` | `10000` | Bound for each backend queue. Must be a positive int. |

Console and file handlers additionally accept `formatter=`; console accepts
`theme=`/`color=`. Broker handlers accept their backend config or `client=`.
See {doc}`../backends/index` for the per-backend argument tables.

## Error handling

Delivery failures (queue full, connection errors, broker send errors) are
reported through the configured error channel instead of being silently dropped.

### Custom error handler

Pass an `error_handler` callback when constructing a handler:

```python
from scietex.logging import ConsoleHandler


def on_error(record, exc):
    # `record` may be None for connection-level failures.
    print(f"Logging error: {exc}")


handler = ConsoleHandler(error_handler=on_error)
```

When no `error_handler` is provided, errors are logged through the
`scietex.logging` module logger.

The `error_handler` callback runs **synchronously on the producer's thread**
inside `emit` (and on the worker for backend delivery failures). Keep it fast
and non-blocking — a slow callback stalls the logging call under exactly the
overload condition that triggers it.

`examples/error_handler_and_queue_bounds.py` combines an `error_handler` with a
bounded queue.

## Queue bounds and overflow

Each backend queue is **bounded** by `queue_maxsize`, a keyword-only constructor
parameter on `AsyncLoggingHandler` and `ConsoleHandler` (default `10000`). It is
validated to a positive int via `validate_queue_maxsize` (invalid values raise
`ValueError`), stored as `self.queue_maxsize`, and applied to every backend
queue the handler registers — the console queue and, for broker handlers, the
broker queue.

```python
handler = ConsoleHandler(queue_maxsize=5000)
```

The overflow policy is **drop + report**. When the shared ingress is full at
emit time, `emit` drops the record and routes a `queue.Full` to the error
channel (`error_handler` callback, or the `scietex.logging` module logger when
none is configured). When a backend queue is full when the bridge re-dispatches,
the bridge reports an `asyncio.QueueFull` instead. `emit` never blocks and never
buffers unboundedly, so the producer stays non-blocking under sustained
overload. Under such overload, records are dropped and reported rather than
buffered without limit. Worst-case buffering is `(1+N) × queue_maxsize` (the
shared ingress plus the N backend queues).

`examples/error_handler_and_queue_bounds.py` demonstrates this drop-and-report
behavior with a small `queue_maxsize`.

## Typed configuration

Every handler builds a frozen dataclass `self.config` from its explicit
constructor keyword arguments (defined in `src/scietex/logging/config.py`).
`LoggingConfig` is the **single runtime source of truth**: handlers read
`self.config.*` at work time, and the flat attributes each handler exposes —
`queue_maxsize`, `error_handler` — are read-only `@property` aliases over
`self.config`, so there is no parallel state to drift.

- `LoggingConfig` — shared machinery options for every handler: `error_handler`
  and `queue_maxsize`. It carries no broker-specific config: each broker handler
  stores its typed config as its own `backend_config` attribute (see below).
- `RedisConfig` — Redis connection settings. It mirrors the full plain-option
  surface of `redis.Redis` (32 fields: `host`/`port`/`db` plus `username`,
  `password`, socket/ssl/encoding/retry/health-check/client-name/protocol
  options), so a `redis_config` dict carrying legitimate client options is
  accepted rather than rejected. Stored as `self.backend_config` on
  `AsyncRedisHandler`.
- `ValkeyConfig` — Valkey connection settings. It mirrors
  `GlideClientConfiguration`'s scalar plain options (`addresses` plus `username`,
  `password`, `use_tls`, `request_timeout`, `database_id`, `client_name`,
  `inflight_requests_limit`, `client_az`, `lazy_connect`, `read_only`);
  enum- and object-valued options are intentionally not modeled. Stored as
  `self.backend_config` on `AsyncValkeyHandler`.
- `MqttConfig` — MQTT connection settings. It mirrors `aiomqtt.Client`'s scalar
  plain options (`host`, `port`, `username`, `password`, `identifier`,
  `keepalive`, `clean_session`, `transport`, `timeout`, `tls_insecure`);
  object-valued expert options (`will`, `tls_context`, `tls_params`,
  `properties`, `logger`) are intentionally not modeled. `host` is the common
  connection field (consistent with `RedisConfig`) and is translated to
  aiomqtt's `hostname` kwarg by `connect()`. Stored as `self.backend_config` on
  `AsyncMqttHandler`.

The handler constructors **do not accept `**kwargs`**. Unknown or typo'd keyword
arguments raise `TypeError` at construction time instead of being silently
swallowed. `AsyncRedisHandler` converts its `redis_config` dict into a typed
`RedisConfig`; keys outside the modeled option surface still raise `TypeError`.
`connect()` reads `self.backend_config` — the single source of truth — rather
than a parallel raw dict; `self.client_config` is a derived, read-only `asdict`
view kept for backward compatibility. The user's `decode_responses` value is
respected, not forced to `True`. For Valkey, `connect()` translates the typed
config into a `GlideClientConfiguration`, dropping `None`-valued fields so glide
applies its own defaults.

## Injecting an external client

Broker handlers accept a keyword-only `client=` argument that injects an
already-created, externally-managed client, so the handler never builds or
closes its own connection. The ownership contract is:

- The handler **never closes** an injected client — the caller owns its lifetime
  and recovery. The handler's `_disconnect()` wrapper is a no-op for an injected
  client (the abstract `disconnect()` is only reached when the handler owns the
  client).
- `client` and the backend config dict (`redis_config` / `valkey_config` /
  `mqtt_config`) are **mutually exclusive**: passing both raises `ValueError`.
- When a client is injected with no config dict, the backend config is unused
  (`connect()` never runs).
- For MQTT, an injected client must already be connected (inside its `async
  with` context) before `start_logging()`, because the handler never enters the
  context on an injected client.

`AsyncFileHandler` and its rotation variants accept a keyword-only `file=`
argument that injects an already-open, externally-managed file-like object, so
the handler never opens or closes its own file. `file=` and `filename` are
mutually exclusive (passing both raises `ValueError`).

See {doc}`../backends/valkey` for the canonical injection discussion and
`examples/injected_client.py` for a runnable example.

## Complete example

```python
import logging
from scietex.logging import ConsoleHandler, ScietexFormatter

logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)

formatter = ScietexFormatter(
    fmt="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
)

handler = ConsoleHandler()
handler.setFormatter(formatter)
logger.addHandler(handler)
```
