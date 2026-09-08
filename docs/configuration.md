# Configuration

This guide covers configuring scietex.logging, including formatters, logger names, and custom formats.

## ScietexFormatter

The `ScietexFormatter` is the default formatter for scietex.logging. It provides:

- Logger name in logs via the `%(name)s` token (read from `record.name`)
- 3-letter log level abbreviations: `DBG`, `INF`, `WRN`, `ERR`, `CRT`
- ISO 8601 UTC timestamps by default

### Basic Usage

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter()
```

With no arguments, `ScietexFormatter` uses the default format
`"%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"`, where `%(name)s`
renders the record's standard-library logger name.

### Custom Format

You can customize the log format by passing a custom `fmt` string:

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter(
    fmt="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

### Date Format

By default, timestamps use ISO 8601 format with UTC timezone. You can customize this:

```python
from scietex.logging import ScietexFormatter
from datetime import datetime, timezone

formatter = ScietexFormatter(datefmt="%Y-%m-%d %H:%M:%S")
```

## Handler Configuration

### Logger Name as Identity

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

### Console Logging

`ConsoleHandler` is a concrete handler that registers the console backend
(`ConsoleBackend`) **unconditionally**. Console output requires adding a
`ConsoleHandler` to the logger explicitly — file and broker handlers do not
register a console sink.

```python
from scietex.logging import ConsoleHandler

handler = ConsoleHandler()
```

For a handler with **no console sink at all**, subclass the pure-machinery base
`AsyncLoggingHandler` directly. It owns the shared
queue/worker/event machinery but registers no backend of its own, so you add
only the backends you want. See `examples/pure_machinery_handler.py` for a
runnable version:

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

### Reserved backend names

`register_backend` rejects a duplicate queue name with `ValueError` (AR-028).
The built-in backends register under `_`-prefixed queue keys, so a custom
`AsyncBrokerHandler` subclass's `queue_name` can never collide with a built-in
backend:

- `"_console"` — registered by `ConsoleHandler` (always).
- `"_redis"` / `"_valkey"` — registered by `AsyncRedisHandler` / `AsyncValkeyHandler`.
- `"_mqtt"` — registered by `AsyncMqttHandler`.
- `"_file"` — registered by `AsyncFileHandler` and its rotation variants.

The `_` prefix is reserved for internal use: user-supplied `queue_name` values
never start with `_`, so any distinct name (e.g. `"postgres"`, `"http"`) is
safe. The shutdown status text strips the `_` before rendering (e.g. "Console
Logger has completed processing its queue.").

### Error Handler

Provide an `error_handler` callback to receive delivery failures (queue full, connection
errors, broker send errors):

```python
def on_error(record, exc):
    print(f"Logging error: {exc}")


handler = ConsoleHandler(error_handler=on_error)
```

See `examples/error_handler_and_queue_bounds.py` for a runnable example that
combines an `error_handler` with a bounded queue.

The `error_handler` callback runs **synchronously on the producer's thread**
inside `emit` (and on the worker for backend delivery failures). Keep it fast
and non-blocking — a slow callback stalls the logging call under exactly the
overload condition that triggers it.

### Queue Bounds and Overflow

Each backend queue is **bounded** by `queue_maxsize`, a keyword-only constructor
parameter on `AsyncLoggingHandler` and `ConsoleHandler` (default `10000`). It
is validated to a positive int via `validate_queue_maxsize` (invalid values
raise `ValueError`), stored as `self.queue_maxsize`, and applied to every
backend queue the handler registers — the console queue and, for broker
handlers, the broker queue.

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

### Typed Configuration

Every handler builds a frozen dataclass `self.config` from its explicit
constructor keyword arguments (defined in `src/scietex/logging/config.py`).
`LoggingConfig` is the **single runtime source of truth**: handlers read
`self.config.*` at work time, and the flat attributes each handler exposes —
`queue_maxsize`, `error_handler` — are read-only `@property`
aliases over `self.config`, so there is no parallel state to drift.

- `LoggingConfig` — shared machinery options for every handler:
  `error_handler`, `queue_maxsize`, and `backend_config` (the
  backend-specific config, or `None` for the pure-machinery/console-only
  handlers). `backend_config` is typed
  `RedisConfig | ValkeyConfig | MqttConfig | None` — a real union, not `Any`.
- `RedisConfig` — Redis connection settings. It mirrors the full plain-option
  surface of `redis.Redis` (32 fields: `host`/`port`/`db` plus `username`,
  `password`, socket/ssl/encoding/retry/health-check/client-name/protocol
  options), so a `redis_config` dict carrying legitimate client options is
  accepted rather than rejected. Stored as `self.config.backend_config` on
  `AsyncRedisHandler`.
- `ValkeyConfig` — Valkey connection settings. It mirrors
  `GlideClientConfiguration`'s scalar plain options (`addresses` plus `use_tls`,
  `request_timeout`, `database_id`, `client_name`, `inflight_requests_limit`,
  `client_az`, `lazy_connect`, `read_only`); enum- and object-valued options are
  intentionally not modeled. Stored as `self.config.backend_config` on
  `AsyncValkeyHandler`.
- `MqttConfig` — MQTT connection settings. It mirrors `aiomqtt.Client`'s scalar
  plain options (`host`, `port`, `username`, `password`, `identifier`,
  `keepalive`, `clean_session`, `transport`, `timeout`, `tls_insecure`);
  object-valued expert options (`will`, `tls_context`, `tls_params`,
  `properties`, `logger`) are intentionally not modeled. `host` is the common
  connection field (consistent with `RedisConfig`) and is translated to
  aiomqtt's `hostname` kwarg by `connect()`. Stored as `self.config.backend_config`
  on `AsyncMqttHandler`.

The handler constructors **no longer accept `**kwargs`**. Unknown or typo'd
keyword arguments now raise `TypeError` at construction time instead of being
silently swallowed. `AsyncRedisHandler` converts its `redis_config` dict into a
typed `RedisConfig`; keys outside the modeled option surface still raise
`TypeError`. `connect()` reads `self.config.backend_config` — the single source
of truth — rather than a parallel raw dict; `self.client_config` is a derived,
read-only `asdict` view kept for backward compatibility. The user's
`decode_responses` value is respected, not forced to `True`. For Valkey,
`connect()` translates the typed config into a `GlideClientConfiguration`,
dropping `None`-valued fields so glide applies its own defaults.

### Injecting an external client

`AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`, and
`AsyncMqttHandler` accept a keyword-only `client=` argument that injects an
already-created, externally-managed client, so the handler never builds or
closes its own connection:

```python
from scietex.logging import AsyncValkeyHandler

client = await GlideClient.create(...)  # app-owned, created by the host
handler = AsyncValkeyHandler(stream_name="logs", client=client)
```

Ownership contract:

- The handler **never closes** an injected client — the caller owns its lifetime
  and recovery. `disconnect()` is a no-op for an injected client.
- `client` and the backend config dict (`redis_config` / `valkey_config` /
  `mqtt_config`) are **mutually exclusive**: passing both raises `ValueError`.
- When a client is injected with no config dict, the backend config is unused
  (`connect()` never runs).
- For MQTT, an injected client must already be connected (inside its `async
  with` context) before `start_logging()`, because the handler never enters the
  context on an injected client.

See `examples/injected_client.py` for a runnable example.

`AsyncFileHandler` and its rotation variants accept a keyword-only `file=`
argument that injects an already-open, externally-managed file-like object, so
the handler never opens or closes its own file. `file=` and `filename` are
mutually exclusive (passing both raises `ValueError`).

### Threading Contract

`emit()` is **thread-safe** and may be called from any thread, including a
thread with no running asyncio loop. It writes the record to a bounded,
thread-safe stdlib `queue.Queue` ingress; a bridge task on the event-loop
thread re-dispatches it into the per-backend queues, so an off-loop `emit()`
**delivers** the record instead of dropping it. `emit()` never raises (a
dropped record is reported through the error channel instead).

### Async write offload (Console and File backends)

Since 1.6.0, the Console and File backends offload their **blocking I/O** off
the event loop. Each backend worker formats the record on the event-loop thread,
then submits the `write`/`flush` (and, for the rotation variants,
rollover/reopen) to a dedicated **single-thread executor** created fresh per
worker run. Formatting stays on the loop (the shared `LogRecord` must not be
mutated off-loop); only the blocking write moves off it. A slow sink — a piped
stdout, a network filesystem, a slow disk — therefore stalls only that executor
thread, never the loop.

Shutdown uses a **`shutdown(wait=True)` teardown contract**:

- On worker teardown (normal stop or cancellation), the file-owning worker's
  outer `finally` submits its stream close to the **same** single-thread
  executor — serialized strictly after any in-flight write — and then calls
  `shutdown(wait=True)`.
- `shutdown(wait=True)` blocks until the in-flight write completes, which
  guarantees **no write-after-close** even when a write outlives the stop
  timeout.
- This is a deliberate **correctness-over-latency** choice: `stop_logging()` may
  return `TIMEOUT` from the queue drain, but it still waits for the in-flight
  write before closing the stream. It is *not* bounded with a timeout, because
  that would reintroduce the write-after-close race.
- The executor is **worker-local** (fresh per start/stop cycle), so handlers
  remain restartable across `start_logging()`/`stop_logging()` cycles.

One consequence of the offload: an injected `file=` object's `write`/`flush` now
run on a non-loop background thread. The handler still never closes an injected
file-like (the caller owns its lifetime), but the file-like must tolerate
cross-thread writes.

## Custom Formatters

You can use Python's standard `logging.Formatter` with custom formats:

```python
import logging
from scietex.logging import ConsoleHandler

formatter = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
)

handler = ConsoleHandler()
handler.setFormatter(formatter)
```

### JSON output

For structured, machine-readable output, use `JsonFormatter` — a
`logging.Formatter` subclass that renders each record as a single-line JSON
object with keys `timestamp` (ISO-8601 UTC), `level`, `logger`, `message`, an
`exception` key (present only when the record carries `exc_info`), plus any
user-added `extra` fields flattened as top-level keys:

```python
from scietex.logging import AsyncFileHandler, JsonFormatter

handler = AsyncFileHandler("app.jsonl", formatter=JsonFormatter())
```

`JsonFormatter` copies the record before formatting (it never mutates the shared
record) and degrades non-serializable extra values to their `repr` rather than
raising.

For a runnable example of customizing `ScietexFormatter` and applying it with
`setFormatter`, see `examples/custom_formatter.py`.

(formatter-scope)=
### Formatter scope: console and file output only

A formatter — whether injected via the `formatter=` constructor keyword or
applied later with `setFormatter` — affects **console (stdout) and file output
only**. It is accepted only by `ConsoleHandler` and `AsyncFileHandler` (and its
rotation variants), which render text through it.

Broker handlers (`AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`,
and any `AsyncBrokerHandler` subclass) do **not** accept a `formatter=` keyword
— passing one raises `TypeError`. They build their wire record from the log
record directly — its `name` field is the record's logger name (`record.name`) —
producing a fixed schema (`level`, `message`, `name`, `time`). That payload is
**invariant** under `setFormatter`, so the broker output is deterministic and
independent of any formatter you install.

Consequences to be aware of:

- On a broker handler, `setFormatter` (inherited from the stdlib `logging.Handler`)
  sets an unused attribute — it has **no visible effect** on broker output.
  Broker handlers no longer register a console sink, and the broker builds its
  payload independently of the formatter. Console output requires adding a
  `ConsoleHandler` to the logger, with the formatter installed there.
- If you need to change what a broker backend sends, that is a property of the
  backend's `send_message` implementation, not of the formatter.

### Formatters must not mutate the record

The same `LogRecord` is fanned out to every backend queue by the bridge task on
the event-loop thread. The shipped `ScietexFormatter` copies the record before
formatting, so it never mutates the shared record. A **custom formatter must do
the same**: if it mutates the record in place (e.g. `record.custom_field = ...`),
the mutation leaks to the broker worker and to any other backend that later
reads the same record. Copy the record first (`import copy; record =
copy.copy(record)`) or avoid mutating it.

## Complete Example

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
