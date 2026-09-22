# Console Backend

Console logging writes formatted log records to standard output. It is always
available — no extra dependency — and is provided by `ConsoleHandler`, which
registers the `ConsoleBackend` peer backend.

## Overview

`ConsoleHandler` is a thin concrete subclass of `AsyncLoggingHandler` that
registers the console backend **unconditionally**. Console output requires
adding a `ConsoleHandler` to the logger explicitly; file and broker handlers do
not register a console sink.

The console sink itself lives in `ConsoleBackend`, a peer backend that owns its
queue, its worker coroutine, and its shutdown-status reporting — exactly the way
`AsyncBrokerHandler` registers its broker backend.

## Quick usage

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

## Configuration

`ConsoleHandler` accepts keyword-only arguments:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `error_handler` | `Callable[[LogRecord \| None, Exception], None] \| None` | `None` | Delivery-error callback; falls back to the `scietex.logging` module logger. |
| `queue_maxsize` | `int` | `10000` | Bound for the console queue. Must be a positive int. |
| `formatter` | `logging.Formatter \| None` | `None` | Formatter for the console sink. Defaults to a `ScietexFormatter`. When supplied, `theme`/`color` are ignored. |
| `theme` | `LoggingTheme \| None` | `None` | Color theme applied to the default `ScietexFormatter`. Only consulted when `formatter` is `None`. |
| `color` | `bool \| None` | `None` | Explicit color override for the default formatter. When `None` and a theme is supplied, color is auto-detected from `sys.stdout` via `resolve_color`. |

The three formatter branches are:

1. `formatter=` supplied → used as-is; `theme`/`color` ignored.
2. `formatter=None`, `theme=None` → plain unthemed `ScietexFormatter` (no color).
3. `formatter=None`, `theme=` supplied → `ScietexFormatter(theme=theme,
   color=color if color is not None else resolve_color(sys.stdout))`.

See {doc}`../guide/themes` for the theme API and {doc}`../guide/formatters` for
formatter scope.

## Behavior notes

- **Formatting on the loop, writing off it.** Each record is formatted on the
  event-loop thread, then the blocking `sys.stdout.write`/`flush` is offloaded
  to a worker-local single-thread executor, so a slow or piped stdout never
  stalls the loop.
- **No stream to close.** The console owns no file handle; on teardown it only
  releases its executor thread (`shutdown(wait=True)` waits for any in-flight
  write).
- **Shutdown status.** After every backend drains, the console's
  `report_status` enqueues synthetic status records (e.g. "Console Logger has
  completed processing its queue.") describing how each backend fared.
- **Color is opt-in.** With no theme the output is monochrome and byte-for-byte
  identical to a plain formatter.

## Full example

`examples/basic_console_logging.py` demonstrates console logging with multiple
levels and logger-name identification. For color, see
`examples/scietex_color_theme.py` and `examples/custom_palette.py`.
