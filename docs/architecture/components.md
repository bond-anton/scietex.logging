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

**Depended on by.** `AsyncBaseHandler` (constructs one in `__init__`);
`AsyncBrokerHandler._worker` (calls `self.formatter.formatTime`); tests.

---

## 3. Base handler — `basic_handler.py`

**Purpose.** Core async logging machinery + the console backend. Subclass of
`logging.Handler`. Owns queues, events, worker tasks, and graceful shutdown.

**Class.** `AsyncBaseHandler(logging.Handler)` — `basic_handler.py:15`

**Public interface.**
- `AsyncBaseHandler(service_name=None, worker_id=None, **kwargs)`
  - kwargs: `stdout_enable` (default True), `queue_put_cleanup_threshold`
    (default 100, clamped to >= 1).
- `async start_logging()` — `basic_handler.py:100`. Sets both events, spawns
  worker tasks.
- `emit(record)` — `basic_handler.py:115`. Synchronous; called by the logging
  framework. No-op if `logging_accept_event` not set. For each queue, schedules
  `asyncio.create_task(queue.put(record))`, tracks the task, and periodically
  prunes completed put tasks.
- `async stop_logging(timeout=5.0)` — `basic_handler.py:152`. Clears accept
  event, drains pending put tasks, clears running event, waits for each
  non-console queue to join (timeout-bounded), drains console queue, gathers
  worker tasks, calls `close()`.
- `_cleanup_queue_put_tasks()` — `basic_handler.py:237`.
- `async _console_logging_worker()` — `basic_handler.py:243`.

**Key instance state.** `stdout_enable`, `formatter` (ScietexFormatter),
`logging_accept_event`, `logging_running_event` (asyncio.Events),
`log_queues: dict[str, asyncio.Queue]`, `log_workers: list[Coroutine]`,
`log_workers_tasks`, `log_queue_put_tasks`, `_queue_put_cleanup_threshold`.

**Depends on.** `formatter.ScietexFormatter`; stdlib `asyncio`, `logging`, `sys`.

**Depended on by.** `AsyncBrokerHandler` (extends); host apps using console
logging; tests.

---

## 4. Broker handler base — `message_broker_handler.py`

**Purpose.** Abstract base for message-broker backends. Adds a named broker
queue + worker on top of `AsyncBaseHandler`, and defines the
connect/disconnect/send_message contract concrete backends implement.

**Class.** `AsyncBrokerHandler(AsyncBaseHandler)` — `message_broker_handler.py:10`

**Public interface.**
- `AsyncBrokerHandler(queue_name, service_name=None, worker_id=None, **kwargs)`
  - Adds `log_queues[queue_name]` and appends `self._worker()` to `log_workers`.
  - `client` attribute (Any | None) — connection slot.
- `async connect()` — `message_broker_handler.py:59`. No-op base; subclass hook.
- `async disconnect()` — `message_broker_handler.py:70`. Base clears `client`.
- `async send_message(record: dict[str, str])` — `message_broker_handler.py:81`.
  No-op base; subclass hook.
- `async _worker()` — `message_broker_handler.py:93`. Calls `connect()`, loops
  draining the broker queue, builds a `dict` log entry, calls `send_message`,
  then `disconnect()` on exit.

**Log-entry dict shape** (built in `_worker`, `message_broker_handler.py:117`):
`{"level": record.levelname, "message": record.getMessage(), "name": logger_name,
"time": formatter.formatTime(record)}`. `logger_name` is `record.worker_name`
if present else `record.name`.

**Depends on.** `basic_handler.AsyncBaseHandler`; stdlib `asyncio`, `datetime`.

**Depended on by.** `AsyncRedisHandler`, `AsyncValkeyHandler` (extend); host
apps implementing custom backends (per docs).

---

## 5. Redis backend — `redis_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Redis stream.

**Class.** `AsyncRedisHandler(AsyncBrokerHandler)` — `redis_handler.py:14`

**Public interface.**
- `AsyncRedisHandler(stream_name, service_name=None, worker_id=None,
  redis_config=None, **kwargs)` — passes `queue_name="redis"` to super.
  `client_config` defaults to `{"host": "localhost", "port": 6379, "db": 0}`.
- `async connect()` — `redis_handler.py:71`. Creates `redis.Redis(**config,
  decode_responses=True)` if `client is None`.
- `async disconnect()` — `redis_handler.py:84`. `await client.aclose()`.
- `async send_message(record)` — `redis_handler.py:92`. `await client.xadd(stream_name, record)`.

**Depends on.** `redis.asyncio` (hard import, raises descriptive ImportError if
absent); `message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 6. Valkey backend — `valkey_handler.py`

**Purpose.** Concrete broker backend writing log entries to a Valkey stream via
the `valkey-glide` client.

**Class.** `AsyncValkeyHandler(AsyncBrokerHandler)` — `valkey_handler.py:14`

**Public interface.**
- `AsyncValkeyHandler(stream_name, service_name=None, worker_id=None,
  valkey_config=None, **kwargs)` — passes `queue_name="valkey"` to super.
  `client_config` defaults to `GlideClientConfiguration([NodeAddress()])`.
- `async connect()` — `valkey_handler.py:71`. `await GlideClient.create(config)`
  if `client is None`; swallows `ClosingError`.
- `async disconnect()` — `valkey_handler.py:86`. `await client.close()`.
- `async send_message(record)` — `valkey_handler.py:94`. `await client.xadd(stream_name, record.items())`.

**Depends on.** `glide` (`ClosingError`, `GlideClient`, `GlideClientConfiguration`,
`NodeAddress`) — hard import, raises descriptive ImportError if absent;
`message_broker_handler.AsyncBrokerHandler`.

**Depended on by.** `__init__.py` (guarded); host apps; tests.

---

## 7. Supporting / non-runtime components

- **Tests** (`tests/`) — depend on the package; Redis/Valkey tests also depend
  on live servers and the third-party clients directly.
- **Examples** (`examples/`) — depend on the package; demonstrate usage.
- **Docs** (`docs/`) — describe intended usage; not code.
- **CI** (`.github/workflows/`) — lint, package/test (with Redis service),
  publish. Not part of runtime architecture.
