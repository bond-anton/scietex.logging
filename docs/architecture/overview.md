# Overview

## What the package is

`scietex.logging` is a library, not an application. It has no standalone
entry point or long-running process of its own. It is imported by a host
application and attached to standard-library `logging.Logger` objects as a
`logging.Handler`. All runtime behavior is driven by the host application
calling the handler's public async methods inside an asyncio event loop.

## Major subsystems / components

The package is a single Python package (`scietex.logging`) with no internal
sub-packages. Architecturally it decomposes into four cooperating layers:

1. **Public API surface** — `src/scietex/logging/__init__.py`
   Re-exports the handler classes and formatter; defines `__version__`.
   Conditionally imports the Redis/Valkey handlers so the base package works
   without optional dependencies.

2. **Formatter layer** — `src/scietex/logging/formatter.py`
   `ScietexFormatter` (a `logging.Formatter`) and the `level_abbreviation`
   helper. Enriches records with a `worker_name` (`service_name:worker_id`)
   and 3-letter level abbreviations; emits ISO-8601 UTC timestamps.

3. **Base handler layer** — `src/scietex/logging/basic_handler.py`
   `AsyncBaseHandler` (a `logging.Handler`). Owns the core async machinery:
   per-backend `asyncio.Queue`s, the accept/running `asyncio.Event`s, worker
   task lifecycle, and the console (stdout) backend worker.

4. **Broker handler layer** — `src/scietex/logging/message_broker_handler.py`
   `AsyncBrokerHandler` (extends `AsyncBaseHandler`). Adds a generic
   "message broker" backend: a named queue, a client connection slot, and an
   abstract `connect` / `disconnect` / `send_message` contract that concrete
   backends implement.

5. **Concrete broker backends**
   - `src/scietex/logging/redis_handler.py` — `AsyncRedisHandler` writes to a
     Redis stream via `redis.asyncio`.
   - `src/scietex/logging/valkey_handler.py` — `AsyncValkeyHandler` writes to
     a Valkey stream via `valkey-glide` (`GlideClient`).

## How the components interact

The interaction model is a **producer/consumer pipeline** layered on the
standard `logging` framework:

```
host app logger
   │  logger.info(...)  →  logging framework calls handler.emit(record)
   ▼
AsyncBaseHandler.emit(record)          [producer, synchronous, non-blocking]
   │  for each backend queue: asyncio.create_task(queue.put(record))
   ▼
per-backend asyncio.Queue              [async boundary]
   │
   ▼
per-backend worker coroutine           [consumer, async]
   ├─ console worker → ScietexFormatter.format → sys.stdout
   └─ broker worker  → build dict → send_message → Redis/Valkey stream
```

Key relationships:

- `AsyncBaseHandler` **depends on** `ScietexFormatter` (constructs one in
  `__init__`).
- `AsyncBrokerHandler` **extends** `AsyncBaseHandler` and reuses its queue /
  event / worker machinery, adding one broker queue + worker.
- `AsyncRedisHandler` and `AsyncValkeyHandler` **extend** `AsyncBrokerHandler`
  and implement the three abstract methods.
- `__init__.py` **depends on** all modules; it is the only place that imports
  the concrete broker handlers, and it does so defensively (try/except
  `ImportError`).

## Application entry points

There is no application entry point. The package is consumed as a library.
The canonical usage pattern (from `docs/index.md`, `examples/*.py`, and the
module docstring in `__init__.py`) is:

1. `logger = logging.getLogger(...)`; `logger.setLevel(...)`.
2. Construct a handler, e.g. `AsyncBaseHandler(service_name=..., worker_id=...)`
   or `AsyncRedisHandler(stream_name=...)`.
3. `logger.addHandler(handler)`.
4. Inside an async context: `await handler.start_logging()`.
5. Log normally (`logger.info(...)`, etc.).
6. `await handler.stop_logging()` to drain and shut down.

The `examples/` directory contains runnable scripts demonstrating this
(`basic_console_logging.py`, `redis_logging.py`, `valkey_logging.py`,
`console_and_redis_logging.py`).

## Important runtime processes

- **Per-handler worker coroutines.** Each handler owns one or more worker
  coroutines, each draining one `asyncio.Queue`:
  - `AsyncBaseHandler._console_logging_worker` (console queue) —
    `basic_handler.py:243`.
  - `AsyncBrokerHandler._worker` (broker queue) — `message_broker_handler.py:93`.
  Workers loop while `logging_running_event` is set **or** their queue is
  non-empty, using a 1-second `asyncio.wait_for` timeout on `queue.get()`.
- **Queue-put tasks.** `emit()` schedules each `queue.put` as an
  `asyncio.Task` and tracks them in `log_queue_put_tasks` for later cleanup /
  drain. This is the mechanism that keeps `emit()` non-blocking.
- **Event-driven gating.** `logging_accept_event` gates `emit()`; 
  `logging_running_event` gates worker loops. Both are set in
  `start_logging()` and cleared in `stop_logging()`.
- **Graceful shutdown.** `stop_logging()` drains pending put tasks, waits for
  each non-console queue to `join()` (with a configurable timeout, default 5s),
  then drains the console queue, gathers worker tasks, and calls `close()`.

## Notable runtime characteristics

- **No global state.** All queues, events, tasks, and the client connection
  are instance attributes. Multiple handlers (even on the same logger) are
  fully independent. See `examples/console_and_redis_logging.py`.
- **Console is a peer backend, not a sink for broker logs.** Each handler
  instance has its own console queue. A broker handler with
  `stdout_enable=True` runs *both* a console worker and a broker worker.
- **Connection lifecycle is per-worker.** The broker worker calls
  `connect()` on start and `disconnect()` on exit (`message_broker_handler.py:104,130`).
