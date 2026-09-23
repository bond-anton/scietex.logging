# MQTT Backend

MQTT logging publishes log records to an MQTT topic. It requires the `mqtt`
extra and is provided by `AsyncMqttHandler`, a concrete `AsyncBrokerHandler`.

## Overview

`AsyncMqttHandler` connects with `aiomqtt`, publishes each record as a JSON
payload to a topic, and reconnects with exponential backoff if the connection
drops. It is a concrete subclass of `AsyncBrokerHandler`, so it inherits the
shared queue/worker machinery and the connect-retry policy.

## Installation

```bash
pip install scietex.logging[mqtt]
```

## Quick usage

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

## Configuration

```python
AsyncMqttHandler(
    topic,
    *,
    mqtt_config=None,
    qos=0,
    retain=False,
    client=None,
    error_handler=None,
    queue_maxsize=10000,
    message_expiry=None,
)
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `topic` | `str` | *(required)* | MQTT topic to publish records to. |
| `mqtt_config` | `dict \| None` | `None` | Connection settings, converted into a typed `MqttConfig`. Mutually exclusive with `client`. |
| `qos` | `int` | `0` | MQTT quality of service (`0`, `1`, or `2`). |
| `retain` | `bool` | `False` | Publish with the MQTT retain flag. |
| `client` | `aiomqtt.Client \| None` | `None` | Externally-managed client. Mutually exclusive with `mqtt_config`. |
| `error_handler` | `Callable \| None` | `None` | Delivery-error callback. |
| `queue_maxsize` | `int` | `10000` | Bound for the MQTT queue. |
| `message_expiry` | `int \| None` | `None` | MQTT 5 message-expiry interval in seconds, attached to every publish as the `MessageExpiryInterval` property. `None` leaves messages without an expiry. |

Passing both `client` and `mqtt_config` raises `ValueError`.

### `MqttConfig`

A frozen dataclass mirroring `aiomqtt.Client`'s scalar plain options. Every
field is optional; unset fields fall back to the client's own defaults.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `host` | `str` | `"localhost"` | Broker hostname (translated to aiomqtt's `hostname`). |
| `port` | `int` | `1883` | Broker port. |
| `username` | `str \| None` | `None` | Auth username. |
| `password` | `str \| None` | `None` | Auth password. |
| `identifier` | `str \| None` | `None` | Client identifier; auto-generated if `None`. |
| `keepalive` | `int \| None` | `None` | Keepalive interval in seconds. |
| `clean_session` | `bool \| None` | `None` | Whether the broker discards the session on disconnect. |
| `transport` | `str \| None` | `None` | `"tcp"`, `"websockets"`, or `"unix"`. |
| `timeout` | `float \| None` | `None` | Default broker-communication timeout. |
| `tls_insecure` | `bool \| None` | `None` | Disable TLS hostname verification. |

Object-valued expert options (`will`, `tls_context`, `tls_params`,
`properties`, `logger`) are intentionally not modeled. `connect()` translates
`host` to aiomqtt's `hostname` kwarg and drops `None`-valued fields so aiomqtt
applies its own defaults.

Unknown keys raise `TypeError` (the dataclass rejects unexpected fields).

```python
from scietex.logging import AsyncMqttHandler

handler = AsyncMqttHandler(
    topic="app/logs",
    mqtt_config={"host": "mqtt.internal", "port": 1883},
    qos=1,
)
```

The handler converts the dict into a typed `MqttConfig` internally (stored as
`self.backend_config`); pass a plain dict, not a `MqttConfig` instance.

## Client injection

Pass an already-connected `aiomqtt.Client` to let the caller own its lifetime
and recovery. The handler never calls `close()` on an injected client:

```python
import aiomqtt
from scietex.logging import AsyncMqttHandler

client = aiomqtt.Client(hostname="localhost", port=1883)
handler = AsyncMqttHandler(topic="app/logs", client=client)
```

When injecting, omit `mqtt_config` — passing both raises `ValueError`. See
{doc}`valkey` for the canonical injection discussion (the contract is identical
across broker backends).

## Behavior notes

- **Wire format.** Each record is published as a JSON payload built from the
  record directly (broker handlers do not accept `formatter=`).
- **Message expiry.** With `message_expiry` set, every publish carries the MQTT 5
  `MessageExpiryInterval` property, so the broker discards an undelivered log
  message after that many seconds. This is the MQTT analogue of the Valkey
  backend's `stream_maxlen`: it bounds how long stale log messages can linger on
  the broker. Leave it `None` for messages that never expire.
- **Reconnect.** On a dropped connection the handler retries with exponential
  backoff — base `0.5s`, capped at `30.0s`, with `0.2` jitter. A successful
  connect resets the backoff counter.
- **Error routing.** Delivery failures go to `error_handler` when supplied,
  otherwise to the `scietex.logging` module logger.
- **Shutdown.** `stop_logging()` drains the queue with a configurable timeout
  (default 5s) and reports per-backend status.

## Full example

`examples/mqtt_logging.py` demonstrates MQTT topic logging.
