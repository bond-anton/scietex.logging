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

3. **Machinery base layer** — `src/scietex/logging/async_logging_handler.py`
   `AsyncLoggingHandler` (a `logging.Handler`). Pure shared machinery with **no
   sink of its own**: per-backend `asyncio.Queue`s, the accept/running
   `asyncio.Event`s, worker task lifecycle, formatter construction, the error
   channel, and a generic `register_backend(name, queue, worker, drain)`
   mechanism. Concrete handlers register their own backends on top of it.

4. **Console backend** — `src/scietex/logging/console_backend.py`
   `ConsoleBackend`. The console (stdout) sink as a **peer backend**: it owns
   its queue, its worker coroutine, and its shutdown-status reporting (the
   synthetic "… has completed processing its queue." records live in its
   `report_status` method, invoked as a post-drain status reporter).

5. **Concrete handler layer** — `src/scietex/logging/basic_handler.py`
   `AsyncBaseHandler` (extends `AsyncLoggingHandler`). A thin concrete subclass
   that registers the console backend as a peer when `stdout_enable=True`.
   Public constructor signatures are unchanged, but `**kwargs` is gone: each
   handler builds a typed `self.config` (`LoggingConfig` etc., from
   `config.py`) from its explicit keyword args, and unknown/typo'd kwargs now
   raise `TypeError` instead of being silently swallowed.

6. **Broker handler layer** — `src/scietex/logging/message_broker_handler.py`
   `AsyncBrokerHandler` (extends `AsyncBaseHandler`, `abc.ABC`). Registers a
   generic "message broker" backend via `register_backend`: a named queue, a
   client connection slot, and an abstract `connect` / `disconnect` /
   `send_message` contract that concrete backends implement.

7. **Concrete broker backends**
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
AsyncLoggingHandler.emit(record)       [producer, synchronous, non-blocking]
   │  for each registered backend queue: queue.put_nowait(record)
   ▼
per-backend asyncio.Queue              [async boundary]
   │
   ▼
per-backend worker coroutine           [consumer, async]
   ├─ ConsoleBackend worker → ScietexFormatter.format → sys.stdout
   └─ broker worker  → build dict → send_message → Redis/Valkey stream
```

Key relationships:

- `AsyncLoggingHandler` **depends on** `ScietexFormatter` (constructs one in
  `__init__`).
- `AsyncBaseHandler` **extends** `AsyncLoggingHandler` and registers a
  `ConsoleBackend` as a peer when `stdout_enable=True`.
- `AsyncBrokerHandler` **extends** `AsyncBaseHandler` and registers its own
  broker queue + worker via `register_backend`.
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
  - `ConsoleBackend._worker` (console queue) — `console_backend.py:86`.
  - `AsyncBrokerHandler._worker` (broker queue) — `message_broker_handler.py:139`.
  Workers loop while `logging_running_event` is set **or** their queue is
  non-empty, using a 1-second `asyncio.wait_for` timeout on `queue.get()`.
- **Synchronous queue puts.** `emit()` calls `queue.put_nowait(record)` on each
  registered backend queue synchronously, so it never blocks on I/O. A failed
  put is reported through the error channel, not swallowed.
- **Event-driven gating.** `logging_accept_event` gates `emit()`; 
  `logging_running_event` gates worker loops. Both are set in
  `start_logging()` and cleared in `stop_logging()`.
- **Graceful shutdown.** `stop_logging()` clears the accept event, then drains
  every registered backend through its per-backend `drain(timeout)` hook in
  registration order (collecting each returned `BackendDrainResult`), invokes
  each registered status reporter with the collected results, and gathers worker
  tasks. It does not call `close()`; the handler may be restarted via
  `start_logging` on the same loop.

## Notable runtime characteristics

- **No global state.** All queues, events, tasks, and the client connection
  are instance attributes. Multiple handlers (even on the same logger) are
  fully independent. See `examples/console_and_redis_logging.py`.
- **Console is a peer backend, not a privileged sink in the base.** The console
  sink lives in `ConsoleBackend`, which `AsyncBaseHandler` registers the same
  way `AsyncBrokerHandler` registers its broker backend. A broker handler with
  `stdout_enable=True` runs *both* a console worker and a broker worker.
- **Connection lifecycle is per-worker.** The broker worker calls
  `connect()` on start and `disconnect()` on exit (`message_broker_handler.py:92,105`).
