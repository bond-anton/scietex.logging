# Lifecycle and Threading

This page covers the handler lifecycle — start, stop, restart — and the
thread-safety contract that lets you log from anywhere.

## The lifecycle

Every handler follows the same lifecycle:

1. Construct the handler and add it to a logger.
2. `await handler.start_logging()` — starts the background worker(s).
3. Log normally.
4. `await handler.stop_logging()` — drains the queues and stops the workers.

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

`start_logging()` and `stop_logging()` are async and must run inside an event
loop. `emit()` is thread-safe and does **not** need a loop.

## Restartability

Handlers are restartable: after `stop_logging()` you may call
`start_logging()` again. The write executor is **worker-local** — created fresh
per start/stop cycle — so no state leaks across cycles.

- `start_logging()` is **not idempotent**: calling it twice without an
  intervening stop raises `RuntimeError`.
- `stop_logging()` **is idempotent**: calling it when already stopped is a
  no-op.

`examples/restartable_lifecycle.py` demonstrates the full lifecycle, including
`stop_logging(timeout)`, idempotent stop, and the `RuntimeError` raised by a
double start.

## Threading contract

`emit()` is **thread-safe** and may be called from any thread, including a
thread with no running asyncio loop. The handler captures the event loop in
`start_logging()` for its bridge task; `emit()` itself writes the record to a
bounded, thread-safe stdlib `queue.Queue` ingress, and a bridge task on the loop
thread re-dispatches it into the per-backend queues. An off-loop `emit()`
therefore **delivers** the record instead of dropping it.

`emit()` never raises — a dropped record is reported through the error channel
instead. This means you can log from worker threads, thread pools, and callbacks
without dropping records or crashing the caller.

## Shutdown and timeouts

Configure the drain timeout when stopping:

```python
await handler.stop_logging(timeout=10.0)  # 10 second timeout
```

`stop_logging()` waits for the queues to drain, up to `timeout` (default 5s).
Each backend reports a `DrainStatus` — `COMPLETED`, `TIMEOUT`, or `ERROR`. The
console and file backends each register a status reporter that writes a
synthetic status line per backend through their own sink (e.g. "Console Logger
has completed processing its queue.") — so the console line appears only when a
`ConsoleHandler` is attached, and a file-only setup writes the status lines to
the file. Broker handlers register no reporter.

### No write-after-close guarantee

Since 1.6.0 the Console and File backends write on a background single-thread
executor, and their teardown uses a `shutdown(wait=True)` contract: even if
`stop_logging(timeout)` returns a `TIMEOUT` from the queue drain, it still waits
for any in-flight `write` to finish before closing the stream — guaranteeing
**no write-after-close**. This is a deliberate correctness-over-latency choice
(the wait is not bounded by the timeout, because bounding it would reintroduce
the write-after-close race).

## Resource management

Always ensure proper cleanup:

```python
async def main():
    handler = ConsoleHandler()
    logger.addHandler(handler)

    await handler.start_logging()
    # ... logging ...
    await handler.stop_logging()  # Ensure all logs are processed
```

## Performance considerations

### High throughput

For high-throughput logging:

1. Increase the backend queue bound via `queue_maxsize` (default 10000). When a
   queue is full, records are dropped and reported through the error channel, so
   a larger bound buffers more before drops begin.
2. Use multiple workers (if implementing a custom handler).

Since 1.6.0 the blocking `write`/`flush` (and rollover/reopen) of the Console
and File backends runs on a dedicated single-thread executor per backend, so a
slow sink no longer stalls the event loop — formatting stays on the loop, only
the blocking I/O moves off it.

See {doc}`configuration` for queue bounds and the drop-and-report overflow
policy.
