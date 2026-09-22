# Valkey Backend

Valkey logging publishes log records to a Valkey stream. It requires the
`valkey` extra and is provided by `AsyncValkeyHandler`, a concrete
`AsyncBrokerHandler`.

## Overview

`AsyncValkeyHandler` connects with `valkey-glide` (`GlideClient`), appends each
record to a Valkey stream via `XADD`, and reconnects with exponential backoff if
the connection drops. It is a concrete subclass of `AsyncBrokerHandler`, so it
inherits the shared queue/worker machinery and the connect-retry policy.

## Installation

```bash
pip install scietex.logging[valkey]
```

## Quick usage

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

## Configuration

```python
AsyncValkeyHandler(
    stream_name,
    *,
    valkey_config=None,
    client=None,
    error_handler=None,
    queue_maxsize=10000,
)
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `stream_name` | `str` | *(required)* | Valkey stream key to append records to. |
| `valkey_config` | `dict \| None` | `None` | Connection settings, converted into a typed `ValkeyConfig`. Mutually exclusive with `client`. |
| `client` | `GlideClient \| None` | `None` | Externally-managed client. Mutually exclusive with `valkey_config`. |
| `error_handler` | `Callable \| None` | `None` | Delivery-error callback. |
| `queue_maxsize` | `int` | `10000` | Bound for the Valkey queue. |

Passing both `client` and `valkey_config` raises `ValueError`.

### `ValkeyConfig`

A frozen dataclass mirroring the `valkey-glide` client option surface. Every
field is optional; unset fields fall back to the client's own defaults.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `addresses` | `list[tuple[str, int]]` | `[("localhost", 6379)]` | `(host, port)` pairs for the Valkey nodes. |
| `username` | `str \| None` | `None` | Auth username. |
| `password` | `str \| None` | `None` | Auth password. |
| `use_tls` | `bool \| None` | `None` | Enable TLS. |
| `request_timeout` | `int \| None` | `None` | Request timeout in milliseconds. |
| `database_id` | `int \| None` | `None` | Database index. |
| `client_name` | `str \| None` | `None` | Client name reported to the server. |
| `inflight_requests_limit` | `int \| None` | `None` | Cap on concurrent in-flight requests. |
| `client_az` | `str \| None` | `None` | Availability zone of the client. |
| `lazy_connect` | `bool \| None` | `None` | Defer connecting until the first command. |
| `read_only` | `bool \| None` | `None` | Connect in read-only mode. |

Enum-typed options (`read_from`, `protocol`, `node_discovery_mode`) and
object-valued options (`reconnect_strategy`, `pubsub_subscriptions`,
`advanced_config`, `compression`, `client_side_cache`, `address_resolver`,
`client_circuit_breaker`) are intentionally not modeled. Optional fields default
to `None`, meaning "let glide apply its own default".

Unknown keys raise `TypeError` (the dataclass rejects unexpected fields).

`connect()` reads the typed config (the single source of truth), translating it
into a `GlideClientConfiguration` and dropping `None`-valued fields so glide
applies its own defaults. The scalar `username` and `password` fields are
combined into a glide `ServerCredentials` and passed as `credentials` to
`GlideClientConfiguration`.

```python
from scietex.logging import AsyncValkeyHandler

handler = AsyncValkeyHandler(
    stream_name="app_logs",
    valkey_config={"addresses": [("valkey.internal", 6379)], "database_id": 1},
)
```

The handler converts the dict into a typed `ValkeyConfig` internally (stored as
`self.backend_config`); pass a plain dict, not a `ValkeyConfig` instance.

## Client injection

Pass an already-connected `GlideClient` to let the caller own its lifetime and
recovery. The handler never calls `close()` on an injected client — this is the
canonical injection contract shared by every broker backend:

```python
from glide import GlideClient, GlideClientConfiguration, NodeAddress
from scietex.logging import AsyncValkeyHandler

config = GlideClientConfiguration(addresses=[NodeAddress(host="localhost", port=6379)])
client = await GlideClient.create(config)
handler = AsyncValkeyHandler(stream_name="app_logs", client=client)
```

When injecting, omit `valkey_config` — passing both raises `ValueError`. The
handler tracks ownership internally (`_owns_client` / `_injected_client`): its
`_connect()` / `_disconnect()` wrappers delegate to the abstract methods only
when the handler owns the client, so an injected client is never closed by the
handler.

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

## Full examples

- `examples/valkey_logging.py` — Valkey stream logging.
- `examples/injected_client.py` — an externally-managed client the handler never
  closes.
