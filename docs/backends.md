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
- `valkey_config`: `GlideClientConfiguration` object for Valkey connection
  (accepted for backward compatibility). The handler stores a typed
  `ValkeyConfig` (a list of `(host, port)` addresses) as
  `self.config.backend_config`; `self.client_config` remains the
  `GlideClientConfiguration` used for the client call.

## Backend Comparison

| Feature | Console | Redis | Valkey |
|---------|---------|-------|--------|
| Dependencies | None | `redis` | `valkey-glide` |
| Installation | Always included | `[redis]` | `[valkey]` |
| Persistence | No | Yes | Yes |
| Throughput | High | High | High |
| Setup Complexity | Low | Medium | Medium |

## Using Multiple Backends

You can use multiple handlers simultaneously:

```python
import logging
from scietex.logging import AsyncBaseHandler, AsyncRedisHandler, AsyncValkeyHandler

logger = logging.getLogger("MultiLogger")
logger.setLevel(logging.DEBUG)

# Console handler (always available)
console_handler = AsyncBaseHandler()

# Redis handler
redis_handler = AsyncRedisHandler(stream_name="logs")

# Valkey handler
valkey_handler = AsyncValkeyHandler(stream_name="logs")

logger.addHandler(console_handler)
logger.addHandler(redis_handler)
logger.addHandler(valkey_handler)
```

For a runnable version that combines console, Redis, and Valkey on one logger
with explicit configs, see `examples/all_backends.py`.

## Backend Architecture

All backends follow the same pattern:

1. Log records are queued via `emit()`
2. Background workers process the queue
3. Records are formatted and sent to the backend
4. Graceful shutdown ensures all records are processed
