# Components

This document describes each major component: purpose, classes/functions,
public interfaces, and dependency relationships. "Depends on" = imports or
constructs; "Depended on by" = who imports/extends it.

---

## 1. Public API surface — `__init__.py`

**Purpose.** Package entry point; re-exports the public classes and defines
the version. Guards optional backend imports so the base package loads without
extras.

**Public interface.** `__all__ = ["AsyncBaseHandler", "AsyncBrokerHandler",
"ScietexFormatter"]`, extended with `"AsyncRedisHandler"` and
`"AsyncValkeyHandler"` when their modules import successfully. `__version__`.

**Depends on.** `basic_handler`, `formatter`, `message_broker_handler`,
`redis_handler` (guarded), `valkey_handler` (guarded).

**Depended on by.** Host applications (`from scietex.logging import ...`);
tests import both from the package root and from submodules.

---

## 2. Formatter — `formatter.py`

**Purpose.** Custom `logging.Formatter` that decorates records with a worker
identity and abbreviated levels, and formats timestamps as ISO-8601 UTC.

**Classes / functions.**
- `ScietexFormatter(logging.Formatter)` — `formatter.py:30`
- `level_abbreviation(log_level: int) -> str` — `formatter.py:9`

**Public interface.**
- `ScietexFormatter(service_name, worker_id=None, fmt=None, datefmt=None)`
  - `worker_name` attribute = `f"{service_name}:{worker_id}"` (worker_id
    defaults to 1).
  - Default `fmt` = `"%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s"`.
  - `formatTime(record, datefmt=None)` — ISO-8601 UTC when `datefmt` is None.
  - `format(record)` — sets `record.worker_name` and `record.levelname`
    (abbreviation) then delegates to `logging.Formatter.format`.
- `level_abbreviation` maps DEBUG/INFO/WARNING/ERROR/CRITICAL → DBG/INF/WRN/ERR/CRT;
  unknown levels → zero-padded 3-digit code.

**Depends on.** stdlib `logging`, `datetime`.

**Depended on by.** `AsyncLoggingHandler` (constructs one in `__init__`);
`AsyncBrokerHandler._worker` (calls `self.formatter.formatTime`); tests.

---

## 3. Machinery base — `async_logging_handler.py`

**Purpose.** Pure shared async machinery with **no sink of its own**. Subclass
of `logging.Handler`. Owns formatter construction, the accept/running events,
per-backend queues/workers, the error channel, and the generic
`register_backend` / `start_logging` / `emit` / `stop_logging` lifecycle.

**Class.** `AsyncLoggingHandler(logging.Handler)` — `async_logging_handler.py:48`

**Public interface.**
- `AsyncLoggingHandler(service_name=None, worker_id=None, *, error_handler=None, queue_maxsize=10000)`
  - Constructs a `ScietexFormatter(service_name, worker_id)`.
  - Builds a typed `self.config = LoggingConfig(...)` from its explicit keyword
    args; `queue_maxsize` is validated to a positive int via
    `validate_queue_maxsize`. No `**kwargs` — unknown keyword args raise
    `TypeError`.
  - Stores `queue_maxsize` as `self.queue_maxsize`; each backend queue is bounded
    by it (default 10000).
- `register_backend(name, queue, worker, drain=None)` —
  `async_logging_handler.py:132`. Registers a backend's queue, worker
  **factory** (zero-argument callable returning a fresh coroutine), and
  optional `drain(timeout, results)` hook.
- `async start_logging()` — `async_logging_handler.py:162`. Sets both events,
  invokes each worker factory and spawns worker tasks. Raises `RuntimeError` if
  already running.
- `emit(record)` — `async_logging_handler.py:187`. Synchronous; called by the
  logging framework. No-op if `logging_accept_event` not set. For each
  registered queue, calls `queue.put_nowait(record)`; a failed put is reported
  through the error channel.
- `async stop_logging(timeout=5.0)` — `async_logging_handler.py:224`. Clears
  both events, then drains every registered backend through its `drain` hook
  (in reverse registration order), gathers worker tasks, and resets
  `log_workers_tasks`. Idempotent (no-op when not running); does **not** call
  `close()`. The handler may be restarted via `start_logging` on the same loop.

**Key instance state.** `formatter` (ScietexFormatter),
`logging_accept_event`, `logging_running_event` (asyncio.Events),
`log_queues: dict[str, asyncio.Queue]`,
`log_worker_factories: list[Callable[[], Coroutine]]`,
`log_workers_tasks`, `_drain_hooks`, `error_handler`, `config` (LoggingConfig).

**Configuration.** Typed config objects live in `config.py`:
`LoggingConfig` (shared machinery options), `RedisConfig`, `ValkeyConfig`
(backend-specific, stored as `config.backend_config`), and the
`validate_queue_maxsize` helper. Every handler builds its `self.config` from its
explicit constructor keyword args; none accept `**kwargs`.

**Depends on.** `formatter.ScietexFormatter`; `config` (`LoggingConfig`,
`validate_queue_maxsize`); stdlib `asyncio`, `logging`.

**Depended on by.** `AsyncBaseHandler` (extends); `ConsoleBackend` (its drain
hook is registered here); `AsyncBrokerHandler` (via `AsyncBaseHandler`).

---

## 4. Console backend — `console_backend.py`

**Purpose.** The console (stdout) sink as a **peer backend**. Owns its queue,
its worker coroutine, and its shutdown-status reporting.

**Class.** `ConsoleBackend` — `console_backend.py:47`

**Public interface.**
- `ConsoleBackend(formatter, running_event, maxsize=10000)` — `console_backend.py:62`.
  Creates its own bounded `asyncio.Queue(maxsize=maxsize)`; holds a reference to
  the handler's formatter and the shared `logging_running_event`.
- `async _worker()` — `console_backend.py:78`. Loops while the running event is
  set or the queue is non-empty, formatting records and writing them to stdout.
- `async drain(timeout, results)` — `console_backend.py:99`. Enqueues a
  synthetic status `LogRecord` for each other backend's drain outcome, then
  drains its own queue.

**Depends on.** stdlib `asyncio`, `logging`, `sys`; `async_logging_handler`
(`BackendDrainResult`, `DrainStatus`).

**Depended on by.** `AsyncBaseHandler` (registers it as a peer backend when
`stdout_enable=True`).

---

## 5. Concrete handler — `basic_handler.py`

**Purpose.** Thin concrete subclass of `AsyncLoggingHandler` that registers the
console backend as a peer. Public signature unchanged.

**Class.** `AsyncBaseHandler(AsyncLoggingHandler)` — `basic_handler.py:15`

**Public interface.**
- `AsyncBaseHandler(service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000)`
  - Builds a typed `self.config = LoggingConfig(...)` (adding `stdout_enable`);
    no `**kwargs` — unknown keyword args raise `TypeError`.
  - When `stdout_enable` is True, constructs a `ConsoleBackend` (with
    `maxsize=queue_maxsize`) and registers it under the name `"console"` via
    `register_backend`.
- Inherits `start_logging`, `emit`, `stop_logging` from `AsyncLoggingHandler`.

**Key instance state.** `stdout_enable`, `_console_backend` (ConsoleBackend |
None).

**Depends on.** `async_logging_handler.AsyncLoggingHandler`;
`console_backend.ConsoleBackend`.

**Depended on by.** `AsyncBrokerHandler` (extends); host apps using console
logging; tests.

---

## 6. Broker handler base — `message_broker_handler.py`

**Purpose.** Abstract base for message-broker backends. Registers a named broker
queue + worker on top of `AsyncBaseHandler`, and defines the
connect/disconnect/send_message contract concrete backends implement.

**Class.** `AsyncBrokerHandler(AsyncBaseHandler, abc.ABC)` — `message_broker_handler.py:13`

**Public interface.**
- `AsyncBrokerHandler(queue_name, service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000)`
  - Registers `log_queues[queue_name]` (a bounded `asyncio.Queue(maxsize=self.queue_maxsize)`)
    and `self._worker` (a bound method used as a worker factory) via
    `register_backend`. No `**kwargs` — unknown keyword args raise `TypeError`.
  - `client` attribute (Any | None) — connection slot.
- `async connect()` — `message_broker_handler.py:63`. Abstract; subclass hook.
- `async disconnect()` — `message_broker_handler.py:76`. Abstract; subclass hook.
- `async send_message(record: dict[str, str])` — `message_broker_handler.py:89`.
  Abstract; subclass hook.
- `async _worker()` — `message_broker_handler.py:104`. Calls `connect()`, loops
  draining the broker queue, builds a `dict` log entry, calls `send_message`,
  then `disconnect()` on exit.
- `async drain(timeout, results)` — `message_broker_handler.py:150`. Waits for
  the broker queue to join and appends a `BackendDrainResult` describing the
  outcome.

**Log-entry dict shape** (built in `_worker`, `message_broker_handler.py:131`):
`{"level": record.levelname, "message": record.getMessage(), "name": logger_name,
"time": formatter.formatTime(record)}`. `logger_name` is `record.worker_name`
if present else `record.name`.

**Depends on.** `basic_handler.AsyncBaseHandler`; stdlib `asyncio`, `datetime`.

**Depended on by.** `AsyncRedisHandler`, `AsyncValkeyHandler` (extend); host
apps implementing custom backends (per docs).

---

## 7. Redis backend — `redis_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Redis stream.

**Class.** `AsyncRedisHandler(AsyncBrokerHandler)` — `redis_handler.py:14`

**Public interface.**
- `AsyncRedisHandler(stream_name, service_name=None, worker_id=None, *,
  redis_config=None, error_handler=None, stdout_enable=True, queue_maxsize=10000)`
  — passes `queue_name="redis"` to super. Converts `redis_config` into a typed
  `RedisConfig` stored as `self.config.backend_config` (unknown dict keys raise
  `TypeError`); `self.client_config` remains a dict for the redis client call,
  defaulting to `{"host": "localhost", "port": 6379, "db": 0}`.
- `async connect()` — `redis_handler.py:71`. Creates `redis.Redis(**config,
  decode_responses=True)` if `client is None`.
- `async disconnect()` — `redis_handler.py:84`. `await client.aclose()`.
- `async send_message(record)` — `redis_handler.py:92`. `await client.xadd(stream_name, record)`.

**Depends on.** `redis.asyncio` (hard import, raises descriptive ImportError if
absent); `message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 8. Valkey backend — `valkey_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Valkey stream via
the `valkey-glide` client.

**Class.** `AsyncValkeyHandler(AsyncBrokerHandler)` — `valkey_handler.py:14`

**Public interface.**
- `AsyncValkeyHandler(stream_name, service_name=None, worker_id=None, *,
  valkey_config=None, error_handler=None, stdout_enable=True, queue_maxsize=10000)`
  — passes `queue_name="valkey"` to super. Stores a typed `ValkeyConfig` (list of
  `(host, port)` addresses) as `self.config.backend_config`; `self.client_config`
  remains a `GlideClientConfiguration`, defaulting to
  `GlideClientConfiguration([NodeAddress()])`.
- `async connect()` — `valkey_handler.py:71`. `await GlideClient.create(config)`
  if `client is None`; swallows `ClosingError`.
- `async disconnect()` — `valkey_handler.py:86`. `await client.close()`.
- `async send_message(record)` — `valkey_handler.py:94`. `await client.xadd(stream_name, record.items())`.

**Depends on.** `glide` (`ClosingError`, `GlideClient`, `GlideClientConfiguration`,
`NodeAddress`) — hard import, raises descriptive ImportError if absent;
`message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 9. Supporting / non-runtime components

- **Tests** (`tests/`) — depend on the package; Redis/Valkey tests also depend
  on live servers and the third-party clients directly.
- **Examples** (`examples/`) — depend on the package; demonstrate usage.
- **Docs** (`docs/`) — describe intended usage; not code.
- **CI** (`.github/workflows/`) — lint, package/test (with Redis service),
  publish. Not part of runtime architecture.
