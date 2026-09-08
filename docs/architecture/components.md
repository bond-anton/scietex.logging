# Components

This document describes each major component: purpose, classes/functions,
public interfaces, and dependency relationships. "Depends on" = imports or
constructs; "Depended on by" = who imports/extends it.

---

## 1. Public API surface — `__init__.py`

**Purpose.** Package entry point; re-exports the public classes and defines
the version. Guards optional backend imports so the base package loads without
extras.

**Public interface.** `__all__ = ["ConsoleHandler", "AsyncBrokerHandler",
"AsyncFileHandler", "AsyncLoggingHandler", "AsyncRotatingFileHandler",
"AsyncTimedRotatingFileHandler", "AsyncWatchedFileHandler", "ConsoleBackend",
"FileBackend", "JsonFormatter", "LoggingConfig", "MqttConfig", "RedisConfig",
"ScietexFormatter", "ValkeyConfig"]`, extended with `"AsyncRedisHandler"`,
`"AsyncValkeyHandler"`, and `"AsyncMqttHandler"` when their modules import
successfully. `__version__`. The config types (`LoggingConfig`, `RedisConfig`,
`ValkeyConfig`, `MqttConfig`) are exported alongside the handler classes and
`ConsoleBackend` / `FileBackend` (AR-035). The file handlers and `JsonFormatter`
are stdlib-only, so they are exported **unconditionally** (no guarded import).

**Depends on.** `async_logging_handler`, `handler/console`, `backend/console`,
`backend/file`, `handler/file`, `formatter/scietex`, `formatter/json`,
`handler/broker`, `handler/redis` (guarded), `handler/valkey` (guarded),
`handler/mqtt` (guarded).

**Depended on by.** Host applications (`from scietex.logging import ...`);
tests import both from the package root and from submodules.

---

## 2. Formatter — `formatter/scietex.py`

**Purpose.** Custom `logging.Formatter` that abbreviates levels and formats
timestamps as ISO-8601 UTC. Identity is not injected by the formatter — it is
the record's standard-library logger name (`record.name`), rendered via the
`%(name)s` format token.

**Classes / functions.**
- `ScietexFormatter(logging.Formatter)` — `formatter/scietex.py:13`
- `level_abbreviation(log_level: int) -> str` — defined in `config.py:170-188`,
  re-exported from `formatter/scietex.py:10` for backward compatibility (AR-026).

**Public interface.**
- `ScietexFormatter(fmt=None, datefmt=None)`
  - Default `fmt` = `"%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"`,
    where `%(name)s` renders the record's standard-library logger name
    (`record.name`) — the sole source of identity.
  - `formatTime(record, datefmt=None)` — ISO-8601 UTC when `datefmt` is None.
  - `format(record)` — copies the record, sets `record.levelname`
    (abbreviation), then delegates to `logging.Formatter.format`.
- `level_abbreviation` maps DEBUG/INFO/WARNING/ERROR/CRITICAL → DBG/INF/WRN/ERR/CRT;
  unknown levels → zero-padded 3-digit code.

**Depends on.** stdlib `logging`, `copy`; `config` (imports `iso_timestamp`,
`level_abbreviation`).

**Depended on by.** `ConsoleHandler` and `AsyncFileHandler` (each constructs one
in `__init__`); `AsyncBrokerHandler._worker` (via `level_abbreviation` from
`config`); tests.

---

## 2a. JSON formatter — `formatter/json.py`

**Purpose.** A `logging.Formatter` subclass that renders each record as a
single-line JSON object (NDJSON), suitable for file sinks and log aggregators.

**Class.** `JsonFormatter(logging.Formatter)` — `formatter/json.py:20`

**Public interface.**
- `JsonFormatter()` — no constructor options; inherits the stdlib
  `logging.Formatter` surface.
- `format(record) -> str` — `formatter/json.py:37`. Copies the record
  (`copy.copy`) so the caller's shared `LogRecord` is never mutated (shared-
  record fan-out discipline, mirroring `ScietexFormatter.format`), then emits a
  single-line JSON object with keys `timestamp` (ISO-8601 UTC), `level` (full
  level name), `logger` (record name), `message` (the rendered message), an
  `exception` key (traceback as a single string) present only when the record
  carries `exc_info`, plus any user-added `extra` fields flattened as top-level
  keys. Stdlib `LogRecord` default attributes are excluded via the module-level
  `_STDLIB_ATTRS` set (`formatter/json.py:17`, captured once from a bare
  `logging.LogRecord`), so `msg`, `args`, `levelno`, `pathname`, `thread`, etc.
  never leak into the line. Non-serializable extra values degrade to their
  `repr` via `json.dumps(default=repr)` rather than raising in the worker.

**Depends on.** stdlib `logging`, `json`, `copy`; `config` (imports
`iso_timestamp` for the ISO-8601 UTC `timestamp`).

**Depended on by.** `__init__.py` (re-export); host apps passing it as a
`formatter=` to `ConsoleHandler` or `AsyncFileHandler` (notably
`AsyncFileHandler`); tests.

---

## 3. Machinery base — `async_logging_handler.py`

**Purpose.** Pure shared async machinery with **no sink of its own**. Subclass
of `logging.Handler`. Owns the accept/running events,
per-backend queues/workers, the error channel, and the generic
`register_backend` / `start_logging` / `emit` / `stop_logging` lifecycle.

**Class.** `AsyncLoggingHandler(logging.Handler)` — `async_logging_handler.py:69`

**Public interface.**
- `AsyncLoggingHandler(*, error_handler=None, queue_maxsize=10000)`
  - Builds a typed `self.config = LoggingConfig(...)` from its explicit keyword
    args; `queue_maxsize` is validated to a positive int via
    `validate_queue_maxsize`. No `**kwargs` — unknown keyword args raise
    `TypeError`. `LoggingConfig` is the single runtime source of truth; the
    flat `queue_maxsize`/`error_handler` attributes are read-only `@property`
    aliases over `self.config`.
- `register_backend(name, queue, worker, drain=None)` —
  `async_logging_handler.py:198`. Registers a backend's queue, worker
  **factory** (zero-argument callable returning a fresh coroutine), and a
  `drain(timeout) -> BackendDrainResult` hook. When `drain` is omitted, a
  generic `queue.join()` drain is registered instead, so a drain-less backend
  still flushes and reports a status result at stop rather than being silently
  dropped (AR-110). Raises `ValueError` if `name` is already registered
  (AR-028).
- `register_status_reporter(reporter)` — `async_logging_handler.py:255`.
  Registers a post-drain observer invoked with the collected
  `BackendDrainResult`s after every backend has drained.
- `async start_logging()` — `async_logging_handler.py:270`. Captures the
  running loop, creates the thread-safe `_ingress`, its `_ingress_event`, and
  the `_bridge_task` (a `_bridge_loop` task, tracked separately from
  `log_workers_tasks`), then sets both events and invokes each worker factory
  and spawns worker tasks. Raises `RuntimeError` if already running.
- `emit(record)` — `async_logging_handler.py:308`. Synchronous and
  **thread-safe**; called by the logging framework. No-op if
  `logging_accept_event` not set (or during the teardown window). Writes the
  record to the shared `_ingress` via `put_nowait` (a `queue.Full` is reported
  through the error channel), then wakes the bridge via
  `loop.call_soon_threadsafe(_ingress_event.set)`.
- `_bridge_loop()` — `async_logging_handler.py:362`. Private coroutine (the
  bridge task). Waits on the ingress event, then drains `_ingress` and
  re-dispatches each record into every backend queue via `put_nowait`. It only
  MOVES records — never formats or mutates them. A full backend queue (or any
  put failure) is reported through the error channel.
- `async stop_logging(timeout=5.0)` — `async_logging_handler.py:400`. Clears
  the accept event, cancels the bridge and flushes the ingress into the backend
  queues **before** the drains run, drains every registered backend
  **concurrently** through its `drain` hook under one shared timeout (collecting
  each returned `BackendDrainResult` in registration order), invokes each
  registered status reporter with the collected results, clears the running
  event, gathers worker tasks, resets `log_workers_tasks`, and clears any
  records still queued after the drain window and worker teardown (`get_nowait()`
  + `task_done()`, `async_logging_handler.py:501-509`) plus any leftover ingress
  entry, so undelivered records are dropped, not replayed (AR-020). Idempotent
  (no-op when not running); does **not** call `close()`. The handler may be
  restarted via `start_logging` on the same loop.
- `close()` — `async_logging_handler.py:520`. Stdlib `logging.shutdown()` hook.
  Marks the handler closed (a later `start_logging()` raises `RuntimeError`) and
  clears the accept event. Does **not** stop workers or close a broker client —
  graceful teardown still requires `await stop_logging()`. `close()` is a
  separate terminal operation for `logging.shutdown()`: it does not call
  `stop_logging()`, and `stop_logging()` does not call `close()`.
- `flush()` — `async_logging_handler.py:536`. Documented no-op; records are
  flushed by the async workers during `stop_logging()`, which cannot be awaited
  from this synchronous hook.

**Key instance state.** `logging_accept_event`, `logging_running_event` (asyncio.Events),
`log_queues: dict[str, asyncio.Queue]`,
`log_worker_factories: list[Callable[[], Coroutine]]`,
`log_workers_tasks`, `_ingress` (thread-safe `queue.Queue`),
`_ingress_event` (asyncio.Event), `_bridge_task` (asyncio.Task),
`_drain_hooks`, `_status_reporters`, `error_handler`,
`config` (LoggingConfig).

**Configuration.** Typed config objects live in `config.py`:
`LoggingConfig` (shared machinery options), `RedisConfig`, `ValkeyConfig`,
`MqttConfig` (backend-specific, stored by the broker handlers as their own
`backend_config` attribute), and the cross-module stdlib-only helpers
`validate_queue_maxsize` and `report_error`. The
`scietex.logging` module logger used by `report_error` also lives in `config.py`
(AR-108), so the handler machinery and the console backend share one
error-routing policy. Every handler builds its `self.config` from its
explicit constructor keyword args; none accept `**kwargs`. `LoggingConfig` is
the single runtime source of truth: handlers read `self.config.*` at work time,
and the flat `queue_maxsize`/`error_handler` attributes are
read-only `@property` aliases over it. The broker-specific config types
(`RedisConfig`, `ValkeyConfig`, `MqttConfig`) are **not** `LoggingConfig`
fields — each broker handler stores its typed config as its own
`backend_config` attribute. `RedisConfig` mirrors the full
plain-option surface of `redis.Redis` (host/port/db plus username/password/
socket/ssl/encoding/retry/health-check/client-name/protocol fields), so
`RedisConfig(**raw)` never rejects a legitimate client option. The file sinks
have **no** `backend_config` seam: their options (`filename`, `mode`,
`encoding`, `delay`, `errors`) are passed directly as constructor keyword args
matching the stdlib `FileHandler` signature, so no `FileConfig` dataclass
exists.

**Depends on.** `config` (`LoggingConfig`, `validate_queue_maxsize`); stdlib
`asyncio`, `logging`, `queue`.

**Depended on by.** `ConsoleHandler` (extends); `ConsoleBackend` (its drain
hook is registered here); `FileBackend` (its drain hook is registered here);
`AsyncFileHandler` (extends); `AsyncBrokerHandler` (extends).

---

## 3a. Write executor helper — `_executor.py`

**Purpose.** A tiny private helper encapsulating the lazy-create / run /
`shutdown(wait=True)` lifecycle of a single-thread executor, so the blocking
write I/O of the Console and File workers can be offloaded off the event loop
without duplicating the lifecycle across all five workers.

**Class.** `_WriteExecutor` — `_executor.py:26` (private; not exported in
`__all__`).

**Public interface.**
- `async run(fn: Callable[[], T]) -> T` — `_executor.py:37`. Runs `fn` on the
  single worker thread (a lazily-created `ThreadPoolExecutor(max_workers=1)`)
  via `loop.run_in_executor` and awaits its completion. `fn` performs the
  blocking I/O for one record (write/flush, or the full rollover+reopen+write
  sequence); formatting must happen on the loop *before* calling this.
- `async shutdown()` — `_executor.py:48`. Waits for any in-flight work, then
  releases the worker thread via `executor.shutdown(wait=True)`. A no-op when
  the executor was never created. After it returns, no write or close is still
  running, so the handler can restart with a fresh executor.

**Why single-threaded + `wait=True`.** The single worker thread serializes every
write and the final close in FIFO order, so a close submitted after an in-flight
write cannot overtake it — this is what prevents a write-after-close race during
shutdown. `shutdown(wait=True)` blocks until the in-flight write completes
(correctness over latency), and the executor is a worker-local (created per
worker run, never stored on the handler) so no thread survives between
start/stop cycles and handlers stay restartable.

**Depends on.** stdlib `asyncio`, `concurrent.futures`. No intra-package
imports.

**Depended on by.** `backend/console` (ConsoleBackend._worker),
`backend/file` (FileBackend._worker and the rotation subclasses).

---

## 4. Console backend — `backend/console.py`

**Purpose.** The console (stdout) sink as a **peer backend**. Owns its
backend-specific worker coroutine; the bounded queue, the `worker` accessor, the
drain hook, and the shutdown-status reporting are inherited from the shared
`_QueueBackend` base.

**Class.** `ConsoleBackend` — `backend/console.py:20` (extends `_QueueBackend`,
`backend/_base.py:59`).

**Public interface.**
- `ConsoleBackend(formatter_provider, running_event, maxsize=10000, error_handler=None)` — `backend/console.py:50`.
  Delegates the queue/`formatter_provider`/`running_event`/`error_handler` state
  to `_QueueBackend.__init__` (which creates the bounded
  `asyncio.Queue(maxsize=maxsize)`). `formatter_provider` is a zero-arg callable
  returning the handler's current formatter, read at work time so the console
  never holds a stale copy (AR-030); `error_handler` is an optional callback
  invoked with `(record, exc)` when a record cannot be written (AR-021).
- `async _worker()` — `backend/console.py:80`. Loops while the running event is
  set or the queue is non-empty, formatting records and writing them to stdout.
  Each record is formatted on the event-loop thread, then the blocking
  `sys.stdout` write+flush is offloaded to a `_WriteExecutor` (single-thread,
  worker-local) so a slow stdout never stalls the loop; the worker's outer
  `finally` calls `executor.shutdown()` (`wait=True`), releasing the worker
  thread after any in-flight write. Format/write failures are routed through
  `_report_error` (the configured `error_handler` or the module logger), with
  `task_done()` in a `finally`.
- Inherited from `_QueueBackend`: `worker` (read-only `@property`,
  `backend/_base.py:118`), returning the bound `_worker` coroutine method as a
  zero-arg worker factory (AR-115); `async drain(timeout) -> BackendDrainResult`
  (`backend/_base.py:134`); `async report_status(results)` (`backend/_base.py:163`),
  enqueuing a synthetic status `LogRecord` for each backend's drain outcome
  (registered by `ConsoleHandler` as a status reporter, so it is invoked by
  `stop_logging` after every backend has drained); `_report_error(record, exc)`
  (`backend/_base.py:127`).

**Depends on.** stdlib `asyncio`, `logging`, `sys`; `async_logging_handler`
(`_QUEUE_CONSOLE`); `_executor` (`_WriteExecutor`); `backend/_base`
(`_QueueBackend`).

**Depended on by.** `ConsoleHandler` (registers it as a peer backend
unconditionally).

---

## 4a. File backend — `backend/file.py`

**Purpose.** The file sink as a **peer backend**, a true peer of `ConsoleBackend`:
it owns its worker coroutine and the entire file lifecycle (lazy open, write,
close-in-finally); the bounded queue, the `worker` accessor, the drain hook, and
the shutdown-status reporting are inherited from `_QueueBackend`. The rotation
subclasses own the stdlib rollover/reopen logic.

**Classes.**
- `FileBackend` — `backend/file.py:33` (extends `_QueueBackend`)
- `RotatingFileBackend(FileBackend)` — `backend/file.py:242`
- `TimedRotatingFileBackend(FileBackend)` — `backend/file.py:326`
- `WatchedFileBackend(FileBackend)` — `backend/file.py:419`

**Public interface.**
- `FileBackend(formatter_provider, running_event, *, filename=None, mode="a", encoding=None, delay=False, errors=None, file=None, maxsize=10000, error_handler=None)` — `backend/file.py:79`.
  Delegates the queue/`formatter_provider`/`running_event`/`error_handler` state
  to `_QueueBackend.__init__`, then holds the entire file-lifecycle state
  (`filename`, `mode`, `encoding`, `delay`, `errors`, `_owns_file`,
  `_injected_file`, `_stream`). `formatter_provider` is a zero-arg callable
  returning the handler's current formatter, read at work time so the backend
  never holds a stale copy (AR-030). `file` and `filename` are mutually
  exclusive (passing both raises `ValueError`). `error_handler` is an optional
  callback invoked with `(record, exc)` when a record cannot be written
  (AR-021).
- `async _worker()` — `backend/file.py:192`. The unified worker for every file
  backend. Loops while the running event is set or the queue is non-empty,
  formatting each record on the event-loop thread and offloading the blocking
  write+flush (and, for the rotation subclasses, rollover/reopen) to a
  worker-local single-thread `_WriteExecutor` so a slow filesystem never stalls
  the loop. When `_needs_record_copy` is set (rotation subclasses), the record is
  copied on the loop thread before it crosses to the executor (the stdlib
  rotator's `shouldRollover` formats it internally); the error channel always
  receives the ORIGINAL record. The worker's outer `finally` submits
  `_close_stream` to the **same** executor (serialized strictly after any
  in-flight write) then `shutdown(wait=True)`, guaranteeing no write-after-close.
- `_open_stream()` / `_close_stream()` — `backend/file.py:141,164`. The file
  lifecycle: open the handle lazily and close it in the worker `finally`,
  honoring injected-file ownership (an injected `file=` is never closed).
- `_write_record(text, record=None)` — `backend/file.py:179`. Opens the stream,
  writes, and flushes on the executor thread; the rotation subclasses override it
  to drive rollover/reopen as one atomic unit on the executor thread.
- Inherited from `_QueueBackend`: `worker` (read-only `@property`,
  `backend/_base.py:118`) returning the bound `_worker` coroutine method as a
  zero-arg worker factory (AR-115); `async drain(timeout) -> BackendDrainResult`
  (`backend/_base.py:134`); `async report_status(results)` (`backend/_base.py:163`),
  enqueuing a synthetic status `LogRecord` for each backend's drain outcome
  (registered by `AsyncFileHandler` as a status reporter, so it is invoked by
  `stop_logging` after every backend has drained); `_report_error(record, exc)`
  (`backend/_base.py:127`).

**Depends on.** stdlib `asyncio`, `logging`, `copy`, `logging.handlers`;
`async_logging_handler` (`_QUEUE_FILE`); `_executor` (`_WriteExecutor`);
`backend/_base` (`_QueueBackend`).

**Depended on by.** `AsyncFileHandler` and its rotation variants (each registers
the matching backend under the name `"_file"`).

---

## 5. Concrete handler — `handler/console.py`

**Purpose.** Thin concrete subclass of `AsyncLoggingHandler` that registers the
console backend as a peer. Public signature unchanged.

**Class.** `ConsoleHandler(AsyncLoggingHandler)` — `handler/console.py:16`

**Public interface.**
- `ConsoleHandler(*, error_handler=None, queue_maxsize=10000, formatter=None)`
  - Builds a typed `self.config = LoggingConfig(...)`; no `**kwargs` — unknown
    keyword args raise `TypeError`. Accepts an optional `formatter=` kwarg
    (default `ScietexFormatter`), stored as `self.formatter` and passed to the
    console backend via `formatter_provider` (AR-024).
  - Constructs a `ConsoleBackend` (with `formatter_provider=lambda:
    self.formatter`, `maxsize=queue_maxsize`, and
    `error_handler=self._report_error`) and registers it under the name
    `"_console"` via `register_backend` (registering `backend.worker` as the
    worker factory, AR-115), and registers the console's `report_status` as a
    status reporter via `register_status_reporter`. Registration is
    **unconditional** — `ConsoleHandler` always registers the console backend.
- Inherits `start_logging`, `emit`, `stop_logging` from `AsyncLoggingHandler`.

**Key instance state.** `_console_backend` (ConsoleBackend | None).

**Depends on.** `async_logging_handler.AsyncLoggingHandler`;
`backend/console.ConsoleBackend`; `formatter/scietex.ScietexFormatter`.

**Depended on by.** host apps using console logging; tests.

---

## 5a. File handler — `handler/file.py`

**Purpose.** Thin concrete subclasses of `AsyncLoggingHandler` that register the
file backend as a peer, mirroring how `ConsoleHandler` registers the console
backend. Each handler builds the right `FileBackend` subclass via an overridable
`_make_backend()` factory and registers the backend's `queue`/`worker`/`drain`;
the backend owns the worker and the entire file lifecycle.

**Classes.**
- `AsyncFileHandler(AsyncLoggingHandler)` — `handler/file.py:28`
- `AsyncRotatingFileHandler(AsyncFileHandler)` — `handler/file.py:150`
- `AsyncTimedRotatingFileHandler(AsyncFileHandler)` — `handler/file.py:238`
- `AsyncWatchedFileHandler(AsyncFileHandler)` — `handler/file.py:335`

**Public interface.**
- `AsyncFileHandler(filename=None, *, mode='a', encoding=None, delay=False, errors=None, file=None, error_handler=None, queue_maxsize=10000, formatter=None)` — `handler/file.py:57`.
  Mirrors the stdlib `logging.FileHandler` signature plus the scietex options.
  Stores `self.formatter` (default `ScietexFormatter`) and builds a `FileBackend`
  via `_make_backend` (passing `formatter_provider=lambda: self.formatter`,
  `maxsize=queue_maxsize`, and `error_handler=self._report_error`), then registers
  it under the name `"_file"` via `register_backend` (registering
  `backend.worker` and `backend.drain` as the worker factory and drain hook,
  AR-115) and registers the backend's `report_status` as a status reporter via
  `register_status_reporter`. `file=` and `filename` are mutually exclusive
  (passing both raises `ValueError`). Registers no console sink.
- `_make_backend(*, filename, mode, encoding, delay, errors, file)` —
  `handler/file.py:120`. Overridable factory returning the `FileBackend` subclass
  for this handler. Rotation variants set their extra options
  (`maxBytes`/`backupCount`, `when`/`interval`/`utc`/`atTime`) **before**
  `super().__init__()` and override `_make_backend` to return the matching
  `RotatingFileBackend`/`TimedRotatingFileBackend`/`WatchedFileBackend`.
- Rotation variants add no methods beyond `_make_backend`; the stdlib rollover is
  driven entirely by the backend worker (the sole writer), never from `emit()`.
  Each backend holds a stdlib handler instance purely for its
  `shouldRollover`/`doRollover`/`reopenIfNeeded` logic, pointing its `.stream` at
  the live stream and adopting it back after a rollover.

**Key instance state.** `formatter`, `_file_backend` (FileBackend); rotation
variants add their rotation options (e.g. `maxBytes`/`backupCount`,
`when`/`interval`/`utc`/`atTime`).

**Depends on.** `async_logging_handler.AsyncLoggingHandler`; `backend/file`
(`FileBackend`, `RotatingFileBackend`, `TimedRotatingFileBackend`,
`WatchedFileBackend`); `formatter/scietex.ScietexFormatter`.

**Depended on by.** `__init__.py` (re-export); host apps using file logging;
tests.

---

## 6. Broker handler base — `handler/broker.py`

**Purpose.** Abstract base for message-broker backends. Registers a named broker
queue + worker on top of `AsyncLoggingHandler`, and defines the
connect/disconnect/send_message contract concrete backends implement.

**Class.** `AsyncBrokerHandler(AsyncLoggingHandler, abc.ABC)` — `handler/broker.py:24`

**Public interface.**
- `AsyncBrokerHandler(queue_name, *, error_handler=None, queue_maxsize=10000, backend_config=None, client=None)`
  - Registers `log_queues[queue_name]` (a bounded `asyncio.Queue(maxsize=self.queue_maxsize)`)
    and `self._worker` (a bound method used as a worker factory) via
    `register_backend`. No `**kwargs` — unknown keyword args raise `TypeError`.
  - `client` attribute (Any | None) — connection slot. When an external `client`
    is injected, the handler stores it durably and never closes it — the caller
    owns its lifetime and recovery. Passing both `client` and `backend_config`
    raises `ValueError`.
  - `client_config` property — read-only `asdict` view of `backend_config`;
    returns `{}` when no config is set (the injected-client case). Defined here
    on the base and inherited by the three concrete broker handlers.
- `async connect()` — `handler/broker.py:128`. Abstract; subclass hook.
- `async disconnect()` — `handler/broker.py:141`. Abstract; subclass hook.
- `async send_message(record: dict[str, str])` — `handler/broker.py:153`.
  Abstract; subclass hook. `record` is a serializable log entry keyed by
  `level`, `message`, `name`, and `time`. Each concrete adapter translates it to
  the argument shape its client expects (Redis `xadd` takes the dict directly;
  Valkey-glide `xadd` takes `record.items()`). This adapter difference is
  intentional and documented. A failure must raise so the worker can report it
  and ack the task; the record is dropped, not retried.
- `async _worker()` — `handler/broker.py:208`. Calls `connect()`, loops
  draining the broker queue, builds a `dict` log entry, calls `send_message`,
  then `disconnect()` on exit. Connect retries use capped exponential backoff
  (AR-022).
- `async drain(timeout) -> BackendDrainResult` — `handler/broker.py:284`.
  Waits for the broker queue to join and returns a `BackendDrainResult`
  describing how the drain concluded.

`AsyncBrokerHandler` extends `AsyncLoggingHandler` directly, so a broker
backend registers **only its own broker backend** — no console sink is
inherited. For console output, add a `ConsoleHandler` to the logger separately.
The built-in backends register under the `_`-prefixed queue keys `"_console"`,
`"_file"`, `"_redis"`, `"_valkey"`, and `"_mqtt"`. The `_` prefix is reserved
for internal use, so a user-supplied `queue_name` never starts with `_` and
cannot collide with a built-in backend (a duplicate `register_backend` name
still raises `ValueError` at construction, AR-028).

**Log-entry dict shape** (built in `_worker`, `handler/broker.py:246-253`):
`{"level": level_abbreviation(record.levelno), "message": record.getMessage(),
"name": record.name,
"time": iso_timestamp(record.created)`.
`level` is computed via `level_abbreviation(record.levelno)` (imported from
`config.py`, AR-026); `name` is the record's standard-library logger name
(`record.name`) and `time` is derived from the record directly, **not** from the
formatter, so the dict is deterministic and invariant under `setFormatter`.

**Depends on.** `async_logging_handler.AsyncLoggingHandler`; `config` (`level_abbreviation`, `iso_timestamp`);
stdlib `abc`, `asyncio`, `dataclasses`, `logging`, `random`.

**Depended on by.** `AsyncRedisHandler`, `AsyncValkeyHandler`,
`AsyncMqttHandler` (extend); host apps implementing custom backends (per docs).

---

## 7. Redis backend — `handler/redis.py`

**Purpose.** Concrete broker backend writing log entries to a Redis stream.

**Class.** `AsyncRedisHandler(AsyncBrokerHandler)` — `handler/redis.py:17`

**Public interface.**
- `AsyncRedisHandler(stream_name, *,
  redis_config=None, client=None, error_handler=None, queue_maxsize=10000)`
  — passes `queue_name="_redis"` to super. Converts `redis_config` into a typed
  `RedisConfig` stored as `self.backend_config`; `RedisConfig` mirrors
  the full plain-option surface of `redis.Redis`, so legitimate client options
  are accepted (unknown keys raise `TypeError`). `self.client_config` is a
  read-only `asdict` view of `backend_config`, defaulting to
  `{"host": "localhost", "port": 6379, "db": 0}`. An injected `client=` is an
  externally-managed
  `redis.Redis` the handler never closes — the caller owns its lifetime and
  recovery; passing both `client` and `redis_config` raises `ValueError`.
- `async connect()` — `handler/redis.py:93`. Builds `redis.Redis(**cfg)` from
  `self.backend_config` if `client is None`, honoring the user's
  `decode_responses` value instead of forcing it, then pings to probe
  connectivity before setting `self.client`. If `ping()` fails, the locally
  created client is closed via `aclose()` before re-raising, so no pool leaks on
  a failed connect (AR-033).
- `async disconnect()` — `handler/redis.py:115`. `await client.aclose()`.
- `async send_message(record)` — `handler/redis.py:123`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise `await client.xadd(stream_name, record)`.
  Redis `xadd` accepts the `dict[str, str]` log entry directly (see the adapter
  note under `AsyncBrokerHandler.send_message`).

**Depends on.** `redis.asyncio` (hard import, raises descriptive ImportError if
absent); `handler/broker.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 8. Valkey backend — `handler/valkey.py`

**Purpose.** Concrete broker backend writing log entries to a Valkey stream via
the `valkey-glide` client.

**Class.** `AsyncValkeyHandler(AsyncBrokerHandler)` — `handler/valkey.py:17`

**Public interface.**
- `AsyncValkeyHandler(stream_name, *,
  valkey_config=None, client=None, error_handler=None, queue_maxsize=10000)`
  — passes `queue_name="_valkey"` to super. `valkey_config` is a **dict**
  (mirroring Redis's seam, AR-025) whose keys mirror
  `GlideClientConfiguration`'s scalar plain options; `addresses` is a list of
  `(host, port)` tuples defaulting to `[("localhost", 6379)]`. A typed
  `ValkeyConfig` is stored as `self.backend_config`; `self.client_config`
  is a read-only `asdict` view of it. An injected `client=` is an
  externally-managed `GlideClient` the
  handler never closes — the caller owns its lifetime and recovery; passing both
  `client` and `valkey_config` raises `ValueError`.
- `async connect()` — `handler/valkey.py:93`. Translates
  `self.backend_config` into a `GlideClientConfiguration` (`addresses`
  tuples → `NodeAddress` objects, `None`-valued fields dropped so glide applies
  its own defaults) and calls `await GlideClient.create(config)` if `client is
  None`.
- `async disconnect()` — `handler/valkey.py:127`. `await client.close()`.
- `async send_message(record)` — `handler/valkey.py:135`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise `await client.xadd(stream_name, record.items())`.
  Valkey-glide `xadd` expects `record.items()` rather than the dict itself — an
  intentional, documented adapter difference (see the adapter note under
  `AsyncBrokerHandler.send_message`).

**Depends on.** `glide` (`GlideClient`, `GlideClientConfiguration`,
`NodeAddress`) — hard import, raises descriptive ImportError if absent;
`handler/broker.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 9. MQTT backend — `handler/mqtt.py`

**Purpose.** Concrete broker backend publishing log records as JSON payloads to
an MQTT topic via the `aiomqtt` client.

**Class.** `AsyncMqttHandler(AsyncBrokerHandler)` — `handler/mqtt.py:18`

**Public interface.**
- `AsyncMqttHandler(topic, *, mqtt_config=None, qos=0, retain=False, client=None, error_handler=None, queue_maxsize=10000)`
  — passes `queue_name="_mqtt"` to super. `mqtt_config` is a **dict** whose keys
  mirror `aiomqtt.Client`'s scalar plain options; a typed `MqttConfig` is stored
  as `self.backend_config` (defaulting to `{"host": "localhost", "port":
  1883}`). `self.client_config` is a read-only `asdict` view of it. An injected
  `client=` is an
  externally-managed `aiomqtt.Client` the handler never closes — the caller owns
  its lifetime and recovery; passing both `client` and `mqtt_config` raises
  `ValueError`. An injected MQTT client must already be connected (inside its
  `async with` context) before `start_logging()`, because the handler never
  enters the context on an injected client.
- `async connect()` — `handler/mqtt.py:106`. Translates
  `self.backend_config` into aiomqtt kwargs (`host` → `hostname`,
  `None`-valued fields dropped so aiomqtt applies its own defaults) and enters
  the client as an async context manager if `client is None`.
- `async disconnect()` — `handler/mqtt.py:128`. `await client.__aexit__(None, None, None)`.
- `async send_message(record)` — `handler/mqtt.py:136`. Raises `RuntimeError`
  when `self.client is None` (AR-034); otherwise
  `await client.publish(self.topic, json.dumps(record), qos=self.qos, retain=self.retain)`.
  The payload is the JSON text of the standard `{level, message, name, time}`
  dict; a subscriber must `json.loads` it.

**Depends on.** `aiomqtt` (hard import, raises descriptive ImportError if
absent); `handler/broker.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 10. Supporting / non-runtime components

- **Tests** (`tests/`) — depend on the package; Redis/Valkey tests also depend
  on live servers and the third-party clients directly.
- **Examples** (`examples/`) — depend on the package; demonstrate usage.
- **Docs** (`docs/`) — describe intended usage; not code.
- **CI** (`.github/workflows/`) — lint, package/test (with Redis service),
  publish. Not part of runtime architecture.
