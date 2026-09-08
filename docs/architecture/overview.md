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

2. **Formatter layer** — `src/scietex/logging/formatter/scietex.py` and
   `src/scietex/logging/formatter/json.py`
   `ScietexFormatter` (a `logging.Formatter`). Abbreviates levels to 3-letter
   codes and emits ISO-8601 UTC timestamps; identity is the record's
   standard-library logger name (`record.name`), rendered via the `%(name)s`
   format token. The `level_abbreviation` helper it uses now lives in
   `config.py` (the neutral leaf) and is re-exported here for backward
   compatibility (AR-026). `JsonFormatter` (a `logging.Formatter`) renders each
   record as a single-line JSON object (NDJSON). Both are stdlib-only.

3. **Machinery base layer** — `src/scietex/logging/async_logging_handler.py`
   `AsyncLoggingHandler` (a `logging.Handler`). Pure shared machinery with **no
   sink of its own**: per-backend `asyncio.Queue`s, the accept/running
   `asyncio.Event`s, worker task lifecycle, the error channel, and a generic
   `register_backend(name, queue, worker, drain)` mechanism. Concrete handlers
   register their own backends on top of it.

4. **Console backend** — `src/scietex/logging/backend/console.py`
   `ConsoleBackend`. The console (stdout) sink as a **peer backend**: it owns
   its queue, its worker coroutine, and its shutdown-status reporting (the
   synthetic "… has completed processing its queue." records live in its
   `report_status` method, invoked as a post-drain status reporter).

5. **File backend** — `src/scietex/logging/backend/file.py`
   `FileBackend` and its rotation subclasses `RotatingFileBackend`,
   `TimedRotatingFileBackend`, and `WatchedFileBackend`. The file sink as a
   **peer backend**, a true peer of `ConsoleBackend`: each owns its queue, its
   worker coroutine, the entire file lifecycle (lazy open, write,
   close-in-finally), and its shutdown-status reporting.

6. **Concrete handler layer** — `src/scietex/logging/handler/console.py`
   `ConsoleHandler` (extends `AsyncLoggingHandler`). A thin concrete subclass
   that registers the console backend as a peer unconditionally.
   Public constructor signatures are unchanged, but `**kwargs` is gone: each
   handler builds a typed `self.config` (`LoggingConfig` etc., from
   `config.py`) from its explicit keyword args, and unknown/typo'd kwargs now
   raise `TypeError` instead of being silently swallowed.

7. **File handler layer** — `src/scietex/logging/handler/file.py`
   `AsyncFileHandler` (extends `AsyncLoggingHandler`). A thin wrapper that
   builds the right `FileBackend` subclass via `_make_backend` and registers its
   `queue`/`worker`/`drain` as a peer, mirroring how `ConsoleHandler` registers
   the console backend. The rotation variants `AsyncRotatingFileHandler`,
   `AsyncTimedRotatingFileHandler`, and `AsyncWatchedFileHandler` subclass it
   and build the matching backend, which reuses the stdlib rollover logic,
   driven from the backend worker (the sole writer).

8. **Broker handler layer** — `src/scietex/logging/handler/broker.py`
   `AsyncBrokerHandler` (extends `AsyncLoggingHandler`, `abc.ABC`). Registers a
   generic "message broker" backend via `register_backend`: a named queue, a
   client connection slot, and an abstract `connect` / `disconnect` /
   `send_message` contract that concrete backends implement.

9. **Concrete broker backends**
   - `src/scietex/logging/handler/redis.py` — `AsyncRedisHandler` writes to a
     Redis stream via `redis.asyncio`.
   - `src/scietex/logging/handler/valkey.py` — `AsyncValkeyHandler` writes to
     a Valkey stream via `valkey-glide` (`GlideClient`).
   - `src/scietex/logging/handler/mqtt.py` — `AsyncMqttHandler` publishes log
     records as JSON payloads to an MQTT topic via `aiomqtt`.

## How the components interact

The interaction model is a **producer/consumer pipeline** layered on the
standard `logging` framework:

```
host app logger
   │  logger.info(...)  →  logging framework calls handler.emit(record)
   ▼
AsyncLoggingHandler.emit(record)       [producer, synchronous, non-blocking, thread-safe]
   │  ingress.put_nowait(record) → loop.call_soon_threadsafe(ingress_event.set)
   ▼
thread-safe queue.Queue ingress        [bounded by queue_maxsize; async boundary]
   │  bridge task drains it and fans out
   ▼
per-backend asyncio.Queue              [async boundary]
   │
   ▼
per-backend worker coroutine           [consumer, async]
   ├─ ConsoleBackend worker → ScietexFormatter.format → single-thread executor → sys.stdout
   ├─ FileBackend worker → formatter.format → single-thread executor → file handle (plain or JSON)
   └─ broker worker  → build dict → send_message → Redis/Valkey stream / MQTT topic
```

Formatting runs on the event-loop thread; the blocking `write`/`flush` (and, for
the rotation backends, rollover/reopen) of the Console and File workers is
offloaded to a per-backend single-thread executor so a slow sink never stalls
the loop. On teardown the file backends submit their stream close to the
same executor and call `shutdown(wait=True)` (no write-after-close).

Key relationships:

- `ConsoleHandler` and `AsyncFileHandler` **depend on** `ScietexFormatter`
  (each constructs one in `__init__`).
- `ConsoleHandler` **extends** `AsyncLoggingHandler` and registers a
  `ConsoleBackend` as a peer unconditionally.
- `AsyncBrokerHandler` **extends** `AsyncLoggingHandler` and registers its own
  broker queue + worker via `register_backend`.
- `AsyncRedisHandler`, `AsyncValkeyHandler`, and `AsyncMqttHandler` **extend**
  `AsyncBrokerHandler` and implement the three abstract methods.
- `__init__.py` **depends on** all modules; it is the only place that imports
  the concrete broker handlers, and it does so defensively (try/except
  `ImportError`).

## Application entry points

There is no application entry point. The package is consumed as a library.
The canonical usage pattern (from `docs/index.md`, `examples/*.py`, and the
module docstring in `__init__.py`) is:

1. `logger = logging.getLogger(...)`; `logger.setLevel(...)`.
2. Construct a handler, e.g. `ConsoleHandler()` or
   `AsyncRedisHandler(stream_name=...)`.
3. `logger.addHandler(handler)`.
4. Inside an async context: `await handler.start_logging()`.
5. Log normally (`logger.info(...)`, etc.).
6. `await handler.stop_logging()` to drain and shut down.

The `examples/` directory contains runnable scripts demonstrating this
(`basic_console_logging.py`, `redis_logging.py`, `valkey_logging.py`,
`mqtt_logging.py`, `file_logging.py`, `console_and_redis_logging.py`).

## Important runtime processes

- **Per-handler worker coroutines.** Each handler owns one or more worker
  coroutines, each draining one `asyncio.Queue`:
  - `ConsoleBackend._worker` (console queue) — `backend/console.py:80`.
  - `FileBackend._worker` (file queue) — `backend/file.py:192`.
  - `AsyncBrokerHandler._worker` (broker queue) — `handler/broker.py:208`.
  Workers loop while `logging_running_event` is set **or** their queue is
  non-empty, using a 1-second `asyncio.wait_for` timeout on `queue.get()`.
- **Blocking-write offload.** The Console and File workers format on the loop
  thread, then submit the blocking `write`/`flush` (and rollover/reopen) to a
  worker-local single-thread `_WriteExecutor` (`_executor.py`) and await it. On
  teardown the file backends submit their stream close to the same
  executor and `shutdown(wait=True)`, so `stop_logging` waits for any in-flight
  write — no write-after-close.
- **Thread-safe ingress write.** `emit()` writes the record to the shared
  thread-safe `queue.Queue` ingress (`put_nowait`) synchronously, so it never
  blocks on I/O, then wakes the bridge via `call_soon_threadsafe`. A full
  ingress is reported through the error channel, not swallowed. The bridge task
  then fans the record out to every backend queue.
- **Event-driven gating.** `logging_accept_event` gates `emit()`; 
  `logging_running_event` gates worker loops. Both are set in
  `start_logging()` and cleared in `stop_logging()`.
- **Graceful shutdown.** `stop_logging()` clears the accept event, cancels the
  bridge and flushes the ingress into the backend queues, then drains every
  registered backend **concurrently** through its per-backend `drain(timeout)`
  hook under one shared timeout (AR-105), collecting each returned
  `BackendDrainResult` in registration order, invokes each registered status
  reporter with the collected results, and gathers worker tasks. It does not
  call `close()`; the handler may be restarted via `start_logging` on the same
  loop.

## Notable runtime characteristics

- **No global state.** All queues, events, tasks, and the client connection
  are instance attributes. Multiple handlers (even on the same logger) are
  fully independent. See `examples/console_and_redis_logging.py`.
- **Console is a peer backend, not a privileged sink in the base.** The console
  sink lives in `ConsoleBackend`, which `ConsoleHandler` registers the same
  way `AsyncBrokerHandler` registers its broker backend. A logger that needs
  both console and broker output attaches a `ConsoleHandler` and a broker
  handler separately.
- **Connection lifecycle is per-worker.** The broker worker calls
  `_connect()` on start and `_disconnect()` on exit (`handler/broker.py:184,197`).
