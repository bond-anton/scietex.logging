# scietex.logging Documentation

**scietex.logging** is an asynchronous logging package designed for
high-performance applications that require non-blocking logging. It uses
`asyncio` to manage log message queues and provides multiple backends, such as
console, file, Redis, Valkey, and MQTT logging.

**Built on the standard `logging` module.** `scietex.logging` is not a
replacement for Python's standard `logging` — it *extends* it. Every handler
subclasses `logging.Handler` (through `AsyncLoggingHandler`), so handlers attach
to ordinary loggers with `logger.addHandler(handler)` and receive records
through the normal `logging` pipeline (`logger.info(...)` → `emit()`).
Formatters subclass `logging.Formatter`. Standard levels, `logger.setLevel()`,
`logger.addHandler()`, `logger.removeHandler()`, and `logging.shutdown()` all
work unchanged, and you can mix `scietex.logging` handlers with standard-library
handlers on the same logger. The only difference is that `scietex.logging`
handlers process records asynchronously instead of synchronously in the calling
thread.

## Features

- **Asynchronous Logging**: Log messages are queued and handled asynchronously,
  reducing impact on application performance.
- **Loop-Independent `emit`**: `emit()` is thread-safe and may be called from any
  thread — including one with no running asyncio loop — so you can log from
  worker threads, thread pools, and callbacks without dropping records.
- **Multiple Backends**: Supports console, file, Redis, Valkey, and MQTT logging
  out of the box.
- **Flexible Logging Levels**: Compatible with Python's standard logging levels
  (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- **Optional Dependencies**: Only installs dependencies for the specific
  backends you need.
- **Opt-in Color**: A small theme API colorizes console output, including
  conversion from a Textual theme.

## Get started

```bash
pip install scietex.logging
```

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

See {doc}`getting-started/installation` for extras and
{doc}`getting-started/quickstart` for a full walkthrough.

```{toctree}
:maxdepth: 2
:caption: Getting Started

getting-started/installation
getting-started/quickstart
```

```{toctree}
:maxdepth: 2
:caption: User Guide

guide/lifecycle
guide/configuration
guide/formatters
guide/themes
guide/textual
guide/custom-backends
```

```{toctree}
:maxdepth: 2
:caption: Backends

backends/index
```

```{toctree}
:maxdepth: 2
:caption: API Reference

api/index
```

```{toctree}
:maxdepth: 1
:caption: Project

examples
changelog
```
