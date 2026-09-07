# Backends

This document details all supported logging backends in scietex.logging.

## Console Logging

Console logging is the default backend and is always available. It outputs log messages to standard output.

### Features

- No additional dependencies required
- Enabled by default in `AsyncBaseHandler`
- Can be disabled with `stdout_enable=False`

### Usage

```python
from scietex.logging import AsyncBaseHandler

handler = AsyncBaseHandler()
```

## Redis Logging

Redis logging sends log records to a Redis stream. Requires the `redis` package.

### Installation

```bash
pip install scietex.logging[redis]
```

### Features

- Persistent log storage in Redis
- High-throughput logging support
- Stream-based architecture

### Usage

```python
from scietex.logging import AsyncRedisHandler

handler = AsyncRedisHandler(
    stream_name="my_log_stream", redis_config={"host": "localhost", "port": 6379, "db": 0}
)
```

### Configuration

- `stream_name`: The Redis stream name (required)
- `redis_config`: Dictionary with Redis connection parameters (accepted for
  backward compatibility). It is converted into a typed `RedisConfig` stored as
  `self.config.backend_config`; unknown keys in the dict raise `TypeError`.
  - `host`: Redis server host (default: "localhost")
  - `port`: Redis server port (default: 6379)
  - `db`: Redis database number (default: 0)
- `client`: Inject an externally-managed `redis.Redis` client the handler never
  closes — the caller owns its lifetime and recovery. Mutually exclusive with
  `redis_config` (passing both raises `ValueError`).

## Valkey Logging

Valkey logging sends log records to a Valkey stream. Requires the `valkey-glide` package.

### Installation

```bash
pip install scietex.logging[valkey]
```

### Features

- Persistent log storage in Valkey
- High-throughput logging support
- Stream-based architecture

### Usage

```python
from scietex.logging import AsyncValkeyHandler

handler = AsyncValkeyHandler(stream_name="my_log_stream")
```

### Configuration

- `stream_name`: The Valkey stream name (required)
- `valkey_config`: Dictionary with Valkey connection parameters (accepted for
  backward compatibility). It is converted into a typed `ValkeyConfig` (mirroring
  `GlideClientConfiguration`'s scalar plain options: `addresses` plus `username`,
  `password`, `use_tls`, `request_timeout`, `database_id`, `client_name`,
  `inflight_requests_limit`, `client_az`, `lazy_connect`, `read_only`) stored as
  `self.config.backend_config`. `connect()` reads that typed config (the single
  source of truth), translating it into a `GlideClientConfiguration` and dropping
  `None`-valued fields so glide applies its own defaults. The scalar `username`
  and `password` fields are combined into a glide `ServerCredentials` and passed
  as `credentials` to `GlideClientConfiguration`.
- `client`: Inject an externally-managed `GlideClient` the handler never closes —
  the caller owns its lifetime and recovery. Mutually exclusive with
  `valkey_config` (passing both raises `ValueError`).

## MQTT Logging

MQTT logging publishes log records as JSON payloads to an MQTT topic. Requires
the `aiomqtt` package.

### Installation

```bash
pip install scietex.logging[mqtt]
```

### Features

- Publish log records to an MQTT topic as JSON
- QoS 0/1/2 delivery guarantees (`qos`)
- Retained-message support (`retain`)
- Client-injection seam for externally-managed connections

### Usage

```python
from scietex.logging import AsyncMqttHandler

handler = AsyncMqttHandler(topic="my/log/topic")
```

### Configuration

- `topic`: The MQTT topic to which log records are published (required).
- `mqtt_config`: Dictionary with MQTT connection parameters (accepted for
  backward compatibility). It is converted into a typed `MqttConfig` stored as
  `self.config.backend_config`; unknown keys in the dict raise `TypeError`.
  `MqttConfig` mirrors `aiomqtt.Client`'s scalar plain options:
  - `host`: MQTT broker host (default: "localhost")
  - `port`: MQTT broker port (default: 1883)
  - `username` / `password`: Authentication credentials (default: None)
  - `identifier`: Client identifier; auto-generated if None
  - `keepalive`: Keepalive interval in seconds (default: None)
  - `clean_session`: Whether the broker discards the session on disconnect
  - `transport`: "tcp", "websockets", or "unix"
  - `timeout`: Default broker-communication timeout
  - `tls_insecure`: Disable TLS hostname verification
  `connect()` translates `host` to aiomqtt's `hostname` kwarg and drops
  `None`-valued fields so aiomqtt applies its own defaults.
- `qos`: The MQTT QoS level (0, 1, or 2) used for publication (default: 0,
  at-most-once fire-and-forget). QoS 1/2 trade throughput for delivery
  guarantees.
- `retain`: Whether published messages are retained by the broker (default:
  False).
- `client`: Inject an externally-managed `aiomqtt.Client` the handler never
  closes — the caller owns its lifetime and recovery. The injected client must
  already be connected (inside its `async with` context) before
  `start_logging()`, because the handler never enters the context on an injected
  client. Mutually exclusive with `mqtt_config` (passing both raises
  `ValueError`).

## File Logging

File logging writes log records to a file. It requires no additional
dependencies and is always available.

### Features

- No additional dependencies required
- Plain-text output by default
- JSON output via `JsonFormatter` (one JSON object per line)
- Rotation variants mirroring the standard-library handlers:
  `AsyncRotatingFileHandler`, `AsyncTimedRotatingFileHandler`,
  `AsyncWatchedFileHandler`
- `file=` injection seam for an externally-managed, already-open file-like

### Usage

```python
from scietex.logging import AsyncFileHandler

handler = AsyncFileHandler("app.log")
```

### JSON output

```python
from scietex.logging import AsyncFileHandler, JsonFormatter

handler = AsyncFileHandler("app.jsonl", formatter=JsonFormatter())
```

### Configuration

- `filename`: Path to the log file (required, unless `file=` is injected).
- `mode`: File open mode (default: "a").
- `encoding`: File encoding (default: None -> locale default).
- `delay`: If True, defer opening the file until the first write (default: False).
- `errors`: Encoding error handling scheme (default: None).
- `file`: Inject an externally-managed, already-open file-like object the
  handler never closes — the caller owns its lifetime and recovery. Mutually
  exclusive with `filename` (passing both raises `ValueError`).

### Async write offload and shutdown guarantee

File writes are **non-blocking to the event loop**: each record is formatted on
the event-loop thread, then the blocking `write`/`flush` (and, for the rotation
variants, rollover/reopen) is offloaded to a dedicated single-thread executor
per handler. A slow sink (network filesystem, slow disk) therefore stalls only
that executor thread, never the loop.

On shutdown, `stop_logging()` guarantees **no write-after-close**: the worker's
teardown submits the stream close to the *same* single-thread executor — so it
is serialized strictly after any in-flight write — then calls
`shutdown(wait=True)`. `stop_logging()` may return a `TIMEOUT` from the queue
drain, but it still waits for the in-flight write to finish before closing the
stream. This is a deliberate correctness-over-latency choice (see
`docs/configuration.md`).

One consequence of the offload: an injected `file=` object's `write`/`flush` now
run on a non-loop background thread. The handler still never closes an injected
file-like (the caller owns its lifetime), but the file-like must tolerate
cross-thread writes.

### Rotation variants

- `AsyncRotatingFileHandler(filename, maxBytes=..., backupCount=...)` — rolls
  over when the file exceeds `maxBytes`.
- `AsyncTimedRotatingFileHandler(filename, when=..., interval=..., backupCount=...)`
  — rolls over on a time interval.
- `AsyncWatchedFileHandler(filename)` — reopens the file if it was rotated or
  deleted externally (e.g. by logrotate).

## Backend Comparison

| Feature | Console | File | Redis | Valkey | MQTT |
|---------|---------|------|-------|--------|------|
| Dependencies | None | None | `redis` | `valkey-glide` | `aiomqtt` |
| Installation | Always included | Always included | `[redis]` | `[valkey]` | `[mqtt]` |
| Persistence | No | Yes | Yes | Yes | No |
| Throughput | High | High | High | High | High |
| Setup Complexity | Low | Low | Medium | Medium | Medium |

## Using Multiple Backends

You can use multiple handlers simultaneously:

```python
import logging
from scietex.logging import (
    AsyncBaseHandler,
    AsyncFileHandler,
    AsyncRedisHandler,
    AsyncValkeyHandler,
    AsyncMqttHandler,
)

logger = logging.getLogger("MultiLogger")
logger.setLevel(logging.DEBUG)

# Console handler (always available)
console_handler = AsyncBaseHandler()

# File handler
file_handler = AsyncFileHandler("logs.log")

# Redis handler
redis_handler = AsyncRedisHandler(stream_name="logs")

# Valkey handler
valkey_handler = AsyncValkeyHandler(stream_name="logs")

# MQTT handler
mqtt_handler = AsyncMqttHandler(topic="logs")

logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.addHandler(redis_handler)
logger.addHandler(valkey_handler)
logger.addHandler(mqtt_handler)
```

For a runnable version that combines console, Redis, and Valkey on one logger
with explicit configs, see `examples/all_backends.py`.

## Backend Architecture

All backends follow the same pattern:

1. Log records are queued via `emit()`
2. Background workers process the queue
3. Records are formatted and sent to the backend
4. Graceful shutdown ensures all records are processed
