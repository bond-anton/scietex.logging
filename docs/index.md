# scietex.logging Documentation

**scietex.logging** is an asynchronous logging package designed for high-performance applications that require non-blocking logging. It uses `asyncio` to manage log message queues and provides multiple backends, such as console, Redis, Valkey, and MQTT logging.

## Features

- **Asynchronous Logging**: Log messages are queued and handled asynchronously, reducing impact on application performance.
- **Loop-Independent `emit`**: `emit()` is thread-safe and may be called from any thread — including one with no running asyncio loop — so you can log from worker threads, thread pools, and callbacks without dropping records.
- **Multiple Backends**: Supports console, file, Redis, Valkey, and MQTT logging out of the box.
- **Flexible Logging Levels**: Compatible with Python's standard logging levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- **Optional Dependencies**: Only installs dependencies for the specific backends you need.

## Installation

Install the base package with:
```bash
pip install scietex.logging
```

To install all optional dependencies (including Redis, Valkey, and MQTT support), use:
```bash
pip install scietex.logging[all]
```

Or, to install individual dependencies as needed:
```bash
pip install scietex.logging[redis]   # For Redis logging
pip install scietex.logging[valkey]  # For Valkey logging
pip install scietex.logging[mqtt]    # For MQTT logging
```

## Quick Start

### Console Logging

Console logging requires adding a `ConsoleHandler` to the logger.

```python
import logging
from scietex.logging import ConsoleHandler
import asyncio

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

### Redis Logging

```python
import logging
from scietex.logging import AsyncRedisHandler
import asyncio

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

### Valkey Logging

```python
import logging
from scietex.logging import AsyncValkeyHandler
import asyncio

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

### MQTT Logging

```python
import logging
from scietex.logging import AsyncMqttHandler
import asyncio

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

### File Logging

```python
import logging
from scietex.logging import AsyncFileHandler, JsonFormatter
import asyncio

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

```{toctree}
:maxdepth: 2
:caption: User Guide

configuration
backends
advanced
examples
```

```{toctree}
:maxdepth: 2
:caption: API Reference

api/index
```

```{toctree}
:maxdepth: 1
:caption: Project

changelog
```
