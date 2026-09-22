# Redis Backend

Redis logging publishes log records to a Redis stream. It requires the `redis`
extra and is provided by `AsyncRedisHandler`, a concrete `AsyncBrokerHandler`.

## Overview

`AsyncRedisHandler` connects with `redis.asyncio`, appends each record to a
Redis stream via `XADD`, and reconnects with exponential backoff if the
connection drops. It is a concrete subclass of `AsyncBrokerHandler`, so it
inherits the shared queue/worker machinery and the connect-retry policy.

## Installation

```bash
pip install scietex.logging[redis]
```

## Quick usage

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

## Configuration

```python
AsyncRedisHandler(
    stream_name,
    *,
    redis_config=None,
    client=None,
    error_handler=None,
    queue_maxsize=10000,
)
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `stream_name` | `str` | *(required)* | Redis stream key to append records to. |
| `redis_config` | `dict \| None` | `None` | Connection settings, converted into a typed `RedisConfig`. Mutually exclusive with `client`. |
| `client` | `redis.asyncio.Redis \| None` | `None` | Externally-managed client. Mutually exclusive with `redis_config`. |
| `error_handler` | `Callable \| None` | `None` | Delivery-error callback. |
| `queue_maxsize` | `int` | `10000` | Bound for the Redis queue. |

Passing both `client` and `redis_config` raises `ValueError`.

### `RedisConfig`

A frozen dataclass mirroring the full plain-option surface of
`redis.asyncio.Redis` (32 fields), so a `redis_config` dict carrying legitimate
client options is accepted rather than rejected. Every field is optional; unset
fields fall back to the client's own defaults.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `host` | `str` | `"localhost"` | Server host. |
| `port` | `int` | `6379` | Server port. |
| `db` | `int` | `0` | Database index. |
| `username` | `str \| None` | `None` | Auth username (Redis 6+ ACL). |
| `password` | `str \| None` | `None` | Auth password. |
| `socket_timeout` | `float \| None` | `None` | Socket timeout in seconds. |
| `socket_connect_timeout` | `float \| None` | `None` | Connect timeout in seconds. |
| `socket_keepalive` | `bool` | `False` | Enable TCP keepalive. |
| `socket_keepalive_options` | `dict \| None` | `None` | TCP keepalive tuning options. |
| `unix_socket_path` | `str \| None` | `None` | Connect over a Unix socket instead of TCP. |
| `encoding` | `str` | `"utf-8"` | Response encoding. |
| `encoding_errors` | `str` | `"strict"` | Encoding error handling scheme. |
| `decode_responses` | `bool` | `False` | Decode byte replies to `str`. |
| `retry_on_error` | `list \| None` | `None` | Exception types to retry on. |
| `ssl` | `bool` | `False` | Enable TLS. |
| `ssl_keyfile` | `str \| None` | `None` | Client private key file. |
| `ssl_certfile` | `str \| None` | `None` | Client certificate file. |
| `ssl_cert_reqs` | `str \| None` | `None` | Certificate verification mode. |
| `ssl_include_verify_flags` | `list \| None` | `None` | Verification flags to include. |
| `ssl_exclude_verify_flags` | `list \| None` | `None` | Verification flags to exclude. |
| `ssl_ca_certs` | `str \| None` | `None` | CA certificate file. |
| `ssl_ca_path` | `str \| None` | `None` | CA certificate directory. |
| `ssl_ca_data` | `str \| None` | `None` | Inline CA certificate data. |
| `ssl_check_hostname` | `bool` | `False` | Verify the server hostname. |
| `ssl_password` | `str \| None` | `None` | Private-key password. |
| `ssl_min_version` | `str \| None` | `None` | Minimum TLS version. |
| `ssl_ciphers` | `str \| None` | `None` | Allowed cipher suite. |
| `max_connections` | `int \| None` | `None` | Connection-pool size cap. |
| `single_connection_client` | `bool` | `False` | Use a single shared connection. |
| `health_check_interval` | `int` | `0` | Seconds between health checks (`0` disables). |
| `client_name` | `str \| None` | `None` | Client name reported to the server. |
| `protocol` | `int \| None` | `None` | RESP protocol version. |

Unknown keys raise `TypeError` (the dataclass rejects unexpected fields).

```python
from scietex.logging import AsyncRedisHandler

handler = AsyncRedisHandler(
    stream_name="app_logs",
    redis_config={"host": "redis.internal", "port": 6379, "db": 1},
)
```

The handler converts the dict into a typed `RedisConfig` internally (stored as
`self.backend_config`); pass a plain dict, not a `RedisConfig` instance.

## Client injection

Pass an already-connected client to let the caller own its lifetime and
recovery. The handler never calls `close()` on an injected client:

```python
import redis.asyncio as redis
from scietex.logging import AsyncRedisHandler

client = redis.Redis(host="localhost", port=6379, decode_responses=True)
handler = AsyncRedisHandler(stream_name="app_logs", client=client)
```

When injecting, omit `redis_config` — passing both raises `ValueError`. See
{doc}`valkey` for the canonical injection discussion (the contract is identical
across broker backends).

## Behavior notes

- **Wire format.** Each record becomes a stream entry; the payload is built from
  the record directly (broker handlers do not accept `formatter=`).
- **Reconnect.** On a dropped connection the handler retries with exponential
  backoff — base `0.5s`, capped at `30.0s`, with `0.2` jitter. A successful
  connect resets the backoff counter.
- **Error routing.** Delivery failures go to `error_handler` when supplied,
  otherwise to the `scietex.logging` module logger.
- **Shutdown.** `stop_logging()` drains the queue with a configurable timeout
  (default 5s) and reports per-backend status.

## Full example

`examples/redis_logging.py` demonstrates Redis stream logging.
