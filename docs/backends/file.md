# File Backend

File logging writes formatted log records to a file. It requires no additional
dependencies and is always available. `AsyncFileHandler` registers the
`FileBackend` peer backend; three rotation variants mirror the standard-library
handlers.

## Overview

`AsyncFileHandler` is a thin wrapper that builds a `FileBackend` — which owns
the worker coroutine and the entire file lifecycle (lazy open, write,
close-in-finally) — and registers the backend's queue, worker, and drain into
the shared machinery, exactly mirroring how `ConsoleHandler` registers
`ConsoleBackend`.

The file handle is opened lazily by the backend worker on first write and closed
when the worker exits, so the file is only open while logging runs.

## Quick usage

```python
import asyncio
import logging

from scietex.logging import AsyncFileHandler

logger = logging.getLogger("MyAsyncLogger")
logger.setLevel(logging.DEBUG)
handler = AsyncFileHandler("app.log")
logger.addHandler(handler)


async def main():
    await handler.start_logging()
    logger.error("This error message will be written to app.log!")
    await handler.stop_logging()


asyncio.run(main())
```

## JSON output

Swap the default `ScietexFormatter` for `JsonFormatter` to write one JSON object
per line (NDJSON):

```python
from scietex.logging import AsyncFileHandler, JsonFormatter

handler = AsyncFileHandler("app.jsonl", formatter=JsonFormatter())
```

See {doc}`../guide/formatters` for the JSON schema.

## Configuration

`AsyncFileHandler` mirrors the standard-library `logging.FileHandler` signature
plus the scietex options:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `filename` | `str \| None` | `None` | Path to the log file. Mutually exclusive with `file=`. |
| `mode` | `str` | `"a"` | File open mode. |
| `encoding` | `str \| None` | `None` | File encoding (locale default when `None`). |
| `delay` | `bool` | `False` | Accepted for stdlib-signature parity; the worker always opens the file lazily on first write regardless of this value. |
| `errors` | `str \| None` | `None` | Encoding error handling scheme. |
| `file` | `Any \| None` | `None` | An externally-managed, already-open file-like object. Mutually exclusive with `filename`. |
| `error_handler` | `Callable \| None` | `None` | Delivery-error callback. |
| `queue_maxsize` | `int` | `10000` | Bound for the file queue. |
| `formatter` | `logging.Formatter \| None` | `None` | Formatter for the file sink. Defaults to a `ScietexFormatter`. |

Passing both `file=` and `filename` raises `ValueError`.

## Rotation variants

All three subclass `AsyncFileHandler` and mirror the stdlib handler of the same
name. Rollover is driven from the backend worker (the sole writer), never from
`emit()`.

### `AsyncRotatingFileHandler`

Rolls over when the file exceeds `maxBytes`, keeping `backupCount` backups.

```python
from scietex.logging import AsyncRotatingFileHandler

handler = AsyncRotatingFileHandler("app.log", maxBytes=10_000_000, backupCount=5)
```

Extra arguments: `maxBytes` (default `0`, disables size rotation),
`backupCount` (default `0`).

### `AsyncTimedRotatingFileHandler`

Rolls over on a time interval.

```python
from scietex.logging import AsyncTimedRotatingFileHandler

handler = AsyncTimedRotatingFileHandler("app.log", when="midnight", backupCount=7)
```

Extra arguments: `when` (`'S'`, `'M'`, `'H'`, `'D'`, `'W0'`–`'W6'`, or
`'midnight'`; default `'h'`), `interval` (default `1`), `backupCount` (default
`0`), `utc` (default `False`), `atTime` (optional rollover time of day).

### `AsyncWatchedFileHandler`

Reopens the file if it was rotated or deleted externally (e.g. by logrotate), so
logging continues to the new inode.

```python
from scietex.logging import AsyncWatchedFileHandler

handler = AsyncWatchedFileHandler("app.log")
```

## Async write offload and shutdown guarantee

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
stream. This is a deliberate correctness-over-latency choice; see
{doc}`../guide/lifecycle`.

One consequence of the offload: an injected `file=` object's `write`/`flush` run
on a non-loop background thread. The handler still never closes an injected
file-like (the caller owns its lifetime), but the file-like must tolerate
cross-thread writes.

## Full example

`examples/file_logging.py` demonstrates plain-text and JSON file output.
