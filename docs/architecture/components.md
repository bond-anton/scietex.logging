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
"AsyncFileHandler", "AsyncLoggingHandler", "AsyncRotatingFileHandler",
"AsyncTimedRotatingFileHandler", "AsyncWatchedFileHandler", "ConsoleBackend",
"FileBackend", "JsonFormatter", "LoggingConfig", "MqttConfig", "RedisConfig",
"ScietexFormatter", "ValkeyConfig"]`, extended with `"AsyncRedisHandler"`,
`"AsyncValkeyHandler"`, and `"AsyncMqttHandler"` when their modules import
successfully. `__version__`. The config types (`LoggingConfig`, `RedisConfig`,
`ValkeyConfig`, `MqttConfig`) are exported alongside the handler classes and
`ConsoleBackend` / `FileBackend` (AR-035). The file handlers and `JsonFormatter`
are stdlib-only, so they are exported **unconditionally** (no guarded import).

**Depends on.** `async_logging_handler`, `basic_handler`, `console_backend`,
`file_backend`, `file_handler`, `formatter`, `json_formatter`,
`message_broker_handler`, `redis_handler` (guarded), `valkey_handler` (guarded),
`mqtt_handler` (guarded).

**Depended on by.** Host applications (`from scietex.logging import ...`);
tests import both from the package root and from submodules.

---

## 2. Formatter — `formatter.py`

**Purpose.** Custom `logging.Formatter` that decorates records with a worker
identity and abbreviated levels, and formats timestamps as ISO-8601 UTC.

**Classes / functions.**
- `ScietexFormatter(logging.Formatter)` — `formatter.py:14`
- `level_abbreviation(log_level: int) -> str` — defined in `config.py:121-139`,
  re-exported from `formatter.py:11` for backward compatibility (AR-026).

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

**Depends on.** stdlib `logging`, `datetime`; `config` (imports
`level_abbreviation`).

**Depended on by.** `AsyncLoggingHandler` (constructs one in `__init__`);
`AsyncBrokerHandler._worker` (via `level_abbreviation` from `config`); tests.

---

## 2a. JSON formatter — `json_formatter.py`

**Purpose.** A `logging.Formatter` subclass that renders each record as a
single-line JSON object (NDJSON), suitable for file sinks and log aggregators.

**Class.** `JsonFormatter(logging.Formatter)` — `json_formatter.py:19`

**Public interface.**
- `JsonFormatter()` — no constructor options; inherits the stdlib
  `logging.Formatter` surface.
- `format(record) -> str` — `json_formatter.py:36`. Copies the record
  (`copy.copy`) so the caller's shared `LogRecord` is never mutated (shared-
  record fan-out discipline, mirroring `ScietexFormatter.format`), then emits a
  single-line JSON object with keys `timestamp` (ISO-8601 UTC), `level` (full
  level name), `logger` (record name), `message` (the rendered message), an
  `exception` key (traceback as a single string) present only when the record
  carries `exc_info`, plus any user-added `extra` fields flattened as top-level
  keys. Stdlib `LogRecord` default attributes are excluded via the module-level
  `_STDLIB_ATTRS` set (`json_formatter.py:16`, captured once from a bare
  `logging.LogRecord`), so `msg`, `args`, `levelno`, `pathname`, `thread`, etc.
  never leak into the line. Non-serializable extra values degrade to their
  `repr` via `json.dumps(default=repr)` rather than raising in the worker.

**Depends on.** stdlib `logging`, `json`, `copy`, `datetime`. No intra-package
imports.

**Depended on by.** `__init__.py` (re-export); host apps passing it as a
`formatter=` to any handler (notably `AsyncFileHandler`); tests.

---

## 3. Machinery base — `async_logging_handler.py`

**Purpose.** Pure shared async machinery with **no sink of its own**. Subclass
of `logging.Handler`. Owns formatter construction, the accept/running events,
per-backend queues/workers, the error channel, and the generic
`register_backend` / `start_logging` / `emit` / `stop_logging` lifecycle.

**Class.** `AsyncLoggingHandler(logging.Handler)` — `async_logging_handler.py:51`

**Public interface.**
- `AsyncLoggingHandler(service_name=None, worker_id=None, *, error_handler=None, queue_maxsize=10000, stdout_enable=True, backend_config=None, formatter=None)`
  - Constructs a `ScietexFormatter(service_name, worker_id)` unless a custom
    `formatter=` is injected (AR-024).
  - Builds a typed `self.config = LoggingConfig(...)` from its explicit keyword
    args; `queue_maxsize` is validated to a positive int via
    `validate_queue_maxsize`. No `**kwargs` — unknown keyword args raise
    `TypeError`. `LoggingConfig` is the single runtime source of truth; the
    flat `queue_maxsize`/`error_handler` attributes are read-only `@property`
    aliases over `self.config`.
- `worker_name` (read-only `@property`) — `async_logging_handler.py:202`.
  Handler identity `f"{config.service_name}:{config.worker_id}"`. `config` is
  the single owner of handler identity; the default `ScietexFormatter` and the
  broker worker both derive from it, so console and broker output cannot diverge
  (AR-107). A user-injected formatter keeps its own `worker_name`.
- `register_backend(name, queue, worker, drain=None)` —
  `async_logging_handler.py:200`. Registers a backend's queue, worker
  **factory** (zero-argument callable returning a fresh coroutine), and a
  `drain(timeout) -> BackendDrainResult` hook. When `drain` is omitted, a
  generic `queue.join()` drain is registered instead, so a drain-less backend
  still flushes and reports a status result at stop rather than being silently
  dropped (AR-110). Raises `ValueError` if `name` is already registered
  (AR-028).
- `register_status_reporter(reporter)` — `async_logging_handler.py:238`.
  Registers a post-drain observer invoked with the collected
  `BackendDrainResult`s after every backend has drained.
- `async start_logging()` — `async_logging_handler.py:253`. Sets both events,
  invokes each worker factory and spawns worker tasks. Raises `RuntimeError` if
  already running.
- `emit(record)` — `async_logging_handler.py:278`. Synchronous; called by the
  logging framework. No-op if `logging_accept_event` not set. For each
  registered queue, calls `queue.put_nowait(record)`; a failed put is reported
  through the error channel.
- `async stop_logging(timeout=5.0)` — `async_logging_handler.py:338`. Clears
  the accept event, drains every registered backend **concurrently** through
  its `drain` hook under one shared timeout (collecting each returned
  `BackendDrainResult` in registration order), invokes each registered status
  reporter with the collected results, clears the running event, gathers worker
  tasks, resets `log_workers_tasks`, and clears any records still queued after
  the drain window and worker teardown (`get_nowait()` + `task_done()`,
  `async_logging_handler.py:415-420`) so undelivered records are dropped, not
  replayed (AR-020). Idempotent (no-op when not running); does **not** call
  `close()`. The handler may be restarted via `start_logging` on the same loop.
- `close()` — `async_logging_handler.py:422`. Stdlib `logging.shutdown()` hook.
  Marks the handler closed (a later `start_logging()` raises `RuntimeError`) and
  clears the accept event. Does **not** stop workers or close a broker client —
  graceful teardown still requires `await stop_logging()`. `close()` is a
  separate terminal operation for `logging.shutdown()`: it does not call
  `stop_logging()`, and `stop_logging()` does not call `close()`.
- `flush()` — `async_logging_handler.py:438`. Documented no-op; records are
  flushed by the async workers during `stop_logging()`, which cannot be awaited
  from this synchronous hook.
- `handleError(record)` — `async_logging_handler.py:448`. Routes a handler-level
  error (e.g. off-loop `emit`) through the single `_report_error` channel via
  `sys.exc_info()[1]`, delivering it to the configured `error_handler` or the
  module logger instead of the stdlib stderr traceback.

**Key instance state.** `formatter` (ScietexFormatter),
`logging_accept_event`, `logging_running_event` (asyncio.Events),
`log_queues: dict[str, asyncio.Queue]`,
`log_worker_factories: list[Callable[[], Coroutine]]`,
`log_workers_tasks`, `_drain_hooks`, `_status_reporters`, `error_handler`,
`config` (LoggingConfig).

**Configuration.** Typed config objects live in `config.py`:
`LoggingConfig` (shared machinery options), `RedisConfig`, `ValkeyConfig`,
`MqttConfig` (backend-specific, stored as `config.backend_config`), and the
cross-module stdlib-only helpers `validate_queue_maxsize` and `report_error`. The
`scietex.logging` module logger used by `report_error` also lives in `config.py`
(AR-108), so the handler machinery and the console backend share one
error-routing policy. Every handler builds its `self.config` from its
explicit constructor keyword args; none accept `**kwargs`. `LoggingConfig` is
the single runtime source of truth: handlers read `self.config.*` at work time,
and the flat `queue_maxsize`/`stdout_enable`/`error_handler` attributes are
read-only `@property` aliases over it. `backend_config` is typed
`RedisConfig | ValkeyConfig | MqttConfig | None`. `RedisConfig` mirrors the full
plain-option surface of `redis.Redis` (host/port/db plus username/password/
socket/ssl/encoding/retry/health-check/client-name/protocol fields), so
`RedisConfig(**raw)` never rejects a legitimate client option. The file sinks
have **no** `backend_config` seam: their options (`filename`, `mode`,
`encoding`, `delay`, `errors`) are passed directly as constructor keyword args
matching the stdlib `FileHandler` signature, so no `FileConfig` dataclass
exists.

**Depends on.** `formatter.ScietexFormatter`; `config` (`LoggingConfig`,
`validate_queue_maxsize`); stdlib `asyncio`, `logging`.

**Depended on by.** `AsyncBaseHandler` (extends); `ConsoleBackend` (its drain
hook is registered here); `FileBackend` (its drain hook is registered here);
`AsyncBrokerHandler` (via `AsyncBaseHandler`).

---

## 4. Console backend — `console_backend.py`

**Purpose.** The console (stdout) sink as a **peer backend**. Owns its queue,
its worker coroutine, and its shutdown-status reporting.

**Class.** `ConsoleBackend` — `console_backend.py:50`

**Public interface.**
- `ConsoleBackend(formatter_provider, running_event, maxsize=10000, error_handler=None)` — `console_backend.py:74`.
  Creates its own bounded `asyncio.Queue(maxsize=maxsize)`; holds the shared
  `logging_running_event`. `formatter_provider` is a zero-arg callable returning
  the handler's current formatter, read at work time so the console never holds
  a stale copy (AR-030); `error_handler` is an optional callback invoked with
  `(record, exc)` when a record cannot be written (AR-021).
- `async _worker()` — `console_backend.py:102`. Loops while the running event is
  set or the queue is non-empty, formatting records and writing them to stdout.
  Format/write failures are routed through `_report_error` (the configured
  `error_handler` or the module logger), with `task_done()` in a `finally`.
- `worker` (read-only `@property`) — `console_backend.py:102`. Returns the bound
  `_worker` coroutine method as a zero-arg worker factory, so `AsyncBaseHandler`
  and custom integrators register the backend's worker without reaching into a
  private attribute (AR-115).
- `async drain(timeout) -> BackendDrainResult` — `console_backend.py:160`. Waits
  for its own queue to drain and returns a `BackendDrainResult` describing how
  the drain concluded.
- `async report_status(results)` — `console_backend.py:183`. Enqueues a
  synthetic status `LogRecord` for each backend's drain outcome. Registered by
  `AsyncBaseHandler` as a status reporter, so it is invoked by `stop_logging`
  after every backend has drained.

**Depends on.** stdlib `asyncio`, `logging`, `sys`; `async_logging_handler`
(`BackendDrainResult`, `DrainStatus`).

**Depended on by.** `AsyncBaseHandler` (registers it as a peer backend when
`stdout_enable=True`).

---

## 4a. File backend — `file_backend.py`

**Purpose.** The file sink as a **peer backend**, cloned from `ConsoleBackend`.
Owns its queue, its worker coroutine, and its shutdown-status reporting. The
only structural difference from the console is the write target: instead of
`sys.stdout`, it writes to whatever `stream_provider()` returns.

**Class.** `FileBackend` — `file_backend.py:49`

**Public interface.**
- `FileBackend(formatter_provider, running_event, stream_provider, maxsize=10000, error_handler=None)` — `file_backend.py:49`.
  Creates its own bounded `asyncio.Queue(maxsize=maxsize)`; holds the shared
  `logging_running_event`. `formatter_provider` is a zero-arg callable returning
  the handler's current formatter, read at work time so the file backend never
  holds a stale copy (AR-030). `stream_provider` is a zero-arg callable
  returning the current writable file object, read at work time so the handler's
  lazily-opened handle is always current. `error_handler` is an optional
  callback invoked with `(record, exc)` when a record cannot be written
  (AR-021).
- `async _worker()` — `file_backend.py:121`. Loops while the running event is
  set or the queue is non-empty, formatting records and writing them to the
  stream returned by `stream_provider()`. Format/write failures are routed
  through `_report_error` (the configured `error_handler` or the module logger),
  with `task_done()` in a `finally`.
- `worker` (read-only `@property`) — `file_backend.py:112`. Returns the bound
  `_worker` coroutine method as a zero-arg worker factory, so `AsyncFileHandler`
  and custom integrators register the backend's worker without reaching into a
  private attribute (AR-115).
- `async drain(timeout) -> BackendDrainResult` — `file_backend.py:158`. Waits
  for its own queue to drain and returns a `BackendDrainResult` describing how
  the drain concluded.
- `async report_status(results)` — `file_backend.py:181`. Enqueues a synthetic
  status `LogRecord` for each backend's drain outcome. Registered by
  `AsyncFileHandler` as a status reporter, so it is invoked by `stop_logging`
  after every backend has drained.

**Depends on.** stdlib `asyncio`, `logging`; `async_logging_handler`
(`BackendDrainResult`, `DrainStatus`); `config` (`report_error`).

**Depended on by.** `AsyncFileHandler` (registers it as a peer backend under the
name `"file"`).

---

## 5. Concrete handler — `basic_handler.py`

**Purpose.** Thin concrete subclass of `AsyncLoggingHandler` that registers the
console backend as a peer. Public signature unchanged.

**Class.** `AsyncBaseHandler(AsyncLoggingHandler)` — `basic_handler.py:16`

**Public interface.**
- `AsyncBaseHandler(service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000, backend_config=None, formatter=None)`
  - Builds a typed `self.config = LoggingConfig(...)` (adding `stdout_enable`);
    no `**kwargs` — unknown keyword args raise `TypeError`. Accepts an optional
    `formatter=` kwarg forwarded to super (AR-024).
  - When `stdout_enable` is True, constructs a `ConsoleBackend` (with
    `formatter_provider=lambda: self.formatter`, `maxsize=queue_maxsize`, and
    `error_handler=self._report_error`) and registers it under the name
    `"console"` via `register_backend` (registering `backend.worker` as the
    worker factory, AR-115), and registers the console's
    `report_status` as a status reporter via `register_status_reporter`.
- Inherits `start_logging`, `emit`, `stop_logging` from `AsyncLoggingHandler`.

**Key instance state.** `stdout_enable` (read-only `@property` alias for
`config.stdout_enable`), `_console_backend` (ConsoleBackend | None).

**Depends on.** `async_logging_handler.AsyncLoggingHandler`;
`console_backend.ConsoleBackend`.

**Depended on by.** `AsyncBrokerHandler` (extends); `AsyncFileHandler` (extends);
host apps using console logging; tests.

---

## 5a. File handler — `file_handler.py`

**Purpose.** Concrete subclass of `AsyncBaseHandler` that registers the file
backend as a peer, mirroring how `AsyncBaseHandler` registers the console
backend. The rotation variants subclass it and reuse the stdlib rollover logic.

**Classes.**
- `AsyncFileHandler(AsyncBaseHandler)` — `file_handler.py:25`
- `AsyncRotatingFileHandler(AsyncFileHandler)` — `file_handler.py:230`
- `AsyncTimedRotatingFileHandler(AsyncFileHandler)` — `file_handler.py:342`
- `AsyncWatchedFileHandler(AsyncFileHandler)` — `file_handler.py:459`

**Public interface.**
- `AsyncFileHandler(filename, service_name=None, worker_id=None, *, mode='a', encoding=None, delay=False, errors=None, file=None, error_handler=None, stdout_enable=True, queue_maxsize=10000, formatter=None)` — `file_handler.py:55`.
  Mirrors the stdlib `logging.FileHandler` signature plus the scietex options.
  When `file` is None, constructs a `FileBackend` (with
  `formatter_provider=lambda: self.formatter`, `stream_provider=lambda:
  self._stream`, `maxsize=queue_maxsize`, and `error_handler=self._report_error`)
  and registers it under the name `"file"` via `register_backend` (registering
  `backend.worker` as the worker factory, AR-115), and registers the file
  backend's `report_status` as a status reporter via
  `register_status_reporter`. `file=` and `filename` are mutually exclusive
  (passing both raises `ValueError`). Inherits the console sink by default
  (`stdout_enable=True`); pass `stdout_enable=False` for a file-only handler.
- `_open_stream()` — `file_handler.py:140`. Opens the file lazily (respecting
  `delay`); when a file-like was injected (`_owns_file` False) it restores the
  durable injected reference and never opens its own file.
- `_close_stream()` — `file_handler.py:163`. Closes the handle the handler
  opened; a no-op for an injected file-like (the caller owns its lifetime).
- `async _worker()` — `file_handler.py:178`. Opens the file lazily on first use,
  loops draining the `"file"` queue and writing formatted records, and closes the
  file in a `finally` so it is released on both normal exit and cancellation.
- `async drain(timeout) -> BackendDrainResult` — `file_handler.py:210`. Waits
  for the `"file"` queue to drain and returns a `BackendDrainResult`.
- Rotation variants add stdlib rollover driven from the worker (the sole
  writer), never from `emit()`: `AsyncRotatingFileHandler` rolls over when the
  file exceeds `maxBytes` (`_worker` at `file_handler.py:307`);
  `AsyncTimedRotatingFileHandler` rolls over on a `when`/`interval` schedule
  (`_worker` at `file_handler.py:424`); `AsyncWatchedFileHandler` reopens the
  file if it was rotated or deleted externally (`_worker` at
  `file_handler.py:523`). Each holds a stdlib handler instance
  (`RotatingFileHandler`/`TimedRotatingFileHandler`/`WatchedFileHandler`) purely
  for its `shouldRollover`/`doRollover`/`reopenIfNeeded` logic, pointing its
  `.stream` at the handler's live stream and adopting it back after a rollover.

**Key instance state.** `filename`, `mode`, `encoding`, `delay`, `errors`,
`_owns_file` (bool), `_injected_file` (Any | None), `_stream` (Any | None),
`_file_backend` (FileBackend | None); rotation variants add `_rotator` /
`_watcher`.

**Depends on.** `basic_handler.AsyncBaseHandler`; `file_backend.FileBackend`;
`async_logging_handler` (`BackendDrainResult`, `DrainStatus`); stdlib
`logging.handlers` (rotation logic).

**Depended on by.** `__init__.py` (re-export); host apps using file logging;
tests.

---

## 6. Broker handler base — `message_broker_handler.py`

**Purpose.** Abstract base for message-broker backends. Registers a named broker
queue + worker on top of `AsyncBaseHandler`, and defines the
connect/disconnect/send_message contract concrete backends implement.

**Class.** `AsyncBrokerHandler(AsyncBaseHandler, abc.ABC)` — `message_broker_handler.py:25`

**Public interface.**
- `AsyncBrokerHandler(queue_name, service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000, backend_config=None, client=None, formatter=None)`
  - Registers `log_queues[queue_name]` (a bounded `asyncio.Queue(maxsize=self.queue_maxsize)`)
    and `self._worker` (a bound method used as a worker factory) via
    `register_backend`. No `**kwargs` — unknown keyword args raise `TypeError`.
    Accepts an optional `formatter=` kwarg forwarded to super (AR-024).
  - `client` attribute (Any | None) — connection slot. When an external `client`
    is injected, the handler stores it durably and never closes it — the caller
    owns its lifetime and recovery. Passing both `client` and `backend_config`
    raises `ValueError`.
- `async connect()` — `message_broker_handler.py:107`. Abstract; subclass hook.
- `async disconnect()` — `message_broker_handler.py:120`. Abstract; subclass hook.
- `async send_message(record: dict[str, str])` — `message_broker_handler.py:132`.
  Abstract; subclass hook. `record` is a serializable log entry keyed by
  `level`, `message`, `name`, and `time`. Each concrete adapter translates it to
  the argument shape its client expects (Redis `xadd` takes the dict directly;
  Valkey-glide `xadd` takes `record.items()`). This adapter difference is
  intentional and documented. A failure must raise so the worker can report it
  and ack the task; the record is dropped, not retried.
- `async _worker()` — `message_broker_handler.py:153`. Calls `connect()`, loops
  draining the broker queue, builds a `dict` log entry, calls `send_message`,
  then `disconnect()` on exit. Connect retries use capped exponential backoff
  (AR-022).
- `async drain(timeout) -> BackendDrainResult` — `message_broker_handler.py:230`.
  Waits for the broker queue to join and returns a `BackendDrainResult`
  describing how the drain concluded.

Because `AsyncBrokerHandler` extends `AsyncBaseHandler`, every broker backend
**inherits the console sink by default** (`stdout_enable=True`); pass
`stdout_enable=False` for a broker-only handler. The queue names `"console"`,
`"file"`, `"redis"`, `"valkey"`, and `"mqtt"` are reserved by the built-in
backends, so a custom `queue_name` must avoid them (a collision raises
`ValueError` at construction, AR-028).

**Log-entry dict shape** (built in `_worker`, `message_broker_handler.py:192-199`):
`{"level": level_abbreviation(record.levelno), "message": record.getMessage(),
"name": self.worker_name,
"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat()}`.
`level` is computed via `level_abbreviation(record.levelno)` (imported from
`config.py`, AR-026); `name` is the handler identity property `worker_name`
(`f"{config.service_name}:{config.worker_id}"`, AR-107) and `time` is derived
from the record directly, **not** from the formatter, so the dict is
deterministic and invariant under `setFormatter`.

**Depends on.** `basic_handler.AsyncBaseHandler`; `config` (`level_abbreviation`);
stdlib `asyncio`, `datetime`.

**Depended on by.** `AsyncRedisHandler`, `AsyncValkeyHandler`,
`AsyncMqttHandler` (extend); host apps implementing custom backends (per docs).

---

## 7. Redis backend — `redis_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Redis stream.

**Class.** `AsyncRedisHandler(AsyncBrokerHandler)` — `redis_handler.py:16`

**Public interface.**
- `AsyncRedisHandler(stream_name, service_name=None, worker_id=None, *,
  redis_config=None, client=None, error_handler=None, stdout_enable=True, queue_maxsize=10000, formatter=None)`
  — passes `queue_name="redis"` to super. Converts `redis_config` into a typed
  `RedisConfig` stored as `self.config.backend_config`; `RedisConfig` mirrors
  the full plain-option surface of `redis.Redis`, so legitimate client options
  are accepted (unknown keys raise `TypeError`). `self.client_config` is a
  read-only `asdict` view of `backend_config`, defaulting to
  `{"host": "localhost", "port": 6379, "db": 0}`. Accepts an optional
  `formatter=` kwarg (AR-024). An injected `client=` is an externally-managed
  `redis.Redis` the handler never closes — the caller owns its lifetime and
  recovery; passing both `client` and `redis_config` raises `ValueError`.
- `async connect()` — `redis_handler.py:91`. Builds `redis.Redis(**cfg)` from
  `self.config.backend_config` if `client is None`, honoring the user's
  `decode_responses` value instead of forcing it, then pings to probe
  connectivity before setting `self.client`. If `ping()` fails, the locally
  created client is closed via `aclose()` before re-raising, so no pool leaks on
  a failed connect (AR-033).
- `async disconnect()` — `redis_handler.py:111`. `await client.aclose()`.
- `async send_message(record)` — `redis_handler.py:119`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise `await client.xadd(stream_name, record)`.
  Redis `xadd` accepts the `dict[str, str]` log entry directly (see the adapter
  note under `AsyncBrokerHandler.send_message`).

**Depends on.** `redis.asyncio` (hard import, raises descriptive ImportError if
absent); `message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 8. Valkey backend — `valkey_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Valkey stream via
the `valkey-glide` client.

**Class.** `AsyncValkeyHandler(AsyncBrokerHandler)` — `valkey_handler.py:16`

**Public interface.**
- `AsyncValkeyHandler(stream_name, service_name=None, worker_id=None, *,
  valkey_config=None, client=None, error_handler=None, stdout_enable=True, queue_maxsize=10000, formatter=None)`
  — passes `queue_name="valkey"` to super. `valkey_config` is a **dict**
  (mirroring Redis's seam, AR-025) whose keys mirror
  `GlideClientConfiguration`'s scalar plain options; `addresses` is a list of
  `(host, port)` tuples defaulting to `[("localhost", 6379)]`. A typed
  `ValkeyConfig` is stored as `self.config.backend_config`; `self.client_config`
  is a read-only `asdict` view of it. Accepts an optional `formatter=` kwarg
  (AR-024). An injected `client=` is an externally-managed `GlideClient` the
  handler never closes — the caller owns its lifetime and recovery; passing both
  `client` and `valkey_config` raises `ValueError`.
- `async connect()` — `valkey_handler.py:94`. Translates
  `self.config.backend_config` into a `GlideClientConfiguration` (`addresses`
  tuples → `NodeAddress` objects, `None`-valued fields dropped so glide applies
  its own defaults) and calls `await GlideClient.create(config)` if `client is
  None`.
- `async disconnect()` — `valkey_handler.py:117`. `await client.close()`.
- `async send_message(record)` — `valkey_handler.py:125`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise `await client.xadd(stream_name, record.items())`.
  Valkey-glide `xadd` expects `record.items()` rather than the dict itself — an
  intentional, documented adapter difference (see the adapter note under
  `AsyncBrokerHandler.send_message`).

**Depends on.** `glide` (`GlideClient`, `GlideClientConfiguration`,
`NodeAddress`) — hard import, raises descriptive ImportError if absent;
`message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 9. MQTT backend — `mqtt_handler.py`

**Purpose.** Concrete broker backend publishing log records as JSON payloads to
an MQTT topic via the `aiomqtt` client.

**Class.** `AsyncMqttHandler(AsyncBrokerHandler)` — `mqtt_handler.py:19`

**Public interface.**
- `AsyncMqttHandler(topic, service_name=None, worker_id=None, *, mqtt_config=None, qos=0, retain=False, client=None, error_handler=None, stdout_enable=True, queue_maxsize=10000, formatter=None)`
  — passes `queue_name="mqtt"` to super. `mqtt_config` is a **dict** whose keys
  mirror `aiomqtt.Client`'s scalar plain options; a typed `MqttConfig` is stored
  as `self.config.backend_config` (defaulting to `{"host": "localhost", "port":
  1883}`). `self.client_config` is a read-only `asdict` view of it. Accepts an
  optional `formatter=` kwarg (AR-024). An injected `client=` is an
  externally-managed `aiomqtt.Client` the handler never closes — the caller owns
  its lifetime and recovery; passing both `client` and `mqtt_config` raises
  `ValueError`. An injected MQTT client must already be connected (inside its
  `async with` context) before `start_logging()`, because the handler never
  enters the context on an injected client.
- `async connect()` — `mqtt_handler.py:129`. Translates
  `self.config.backend_config` into aiomqtt kwargs (`host` → `hostname`,
  `None`-valued fields dropped so aiomqtt applies its own defaults) and enters
  the client as an async context manager if `client is None`.
- `async disconnect()` — `mqtt_handler.py:151`. `await client.__aexit__(None, None, None)`.
- `async send_message(record)` — `mqtt_handler.py:159`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise
  `await client.publish(self.topic, json.dumps(record), qos=self.qos, retain=self.retain)`.
  The payload is the JSON text of the standard `{level, message, name, time}`
  dict; a subscriber must `json.loads` it.

**Depends on.** `aiomqtt` (hard import, raises descriptive ImportError if
absent); `message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 10. Supporting / non-runtime components

- **Tests** (`tests/`) — depend on the package; Redis/Valkey tests also depend
  on live servers and the third-party clients directly.
- **Examples** (`examples/`) — depend on the package; demonstrate usage.
- **Docs** (`docs/`) — describe intended usage; not code.
- **CI** (`.github/workflows/`) — lint, package/test (with Redis service),
  publish. Not part of runtime architecture.
