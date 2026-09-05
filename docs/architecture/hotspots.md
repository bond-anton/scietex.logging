# Hotspots

Areas that deserve deeper architectural investigation. This is **not** a
critique or a refactor proposal — it flags structurally significant locations
for the later deep review. Each entry gives location, what the code appears to
do, why it is significant, and related files.

---

## 1. `AsyncLoggingHandler` — the shared machinery core

**Location.** `src/scietex/logging/async_logging_handler.py` (`AsyncLoggingHandler`, 267-line module).

**What it appears to do.** One class owns the stdlib `logging.Handler`
integration (`emit`), the async queue/event machinery, worker task lifecycle,
the generic `register_backend` mechanism, and the generic `stop_logging` that
drains every backend through per-backend `drain(timeout, results)` hooks.

**Why significant.** It is the backend-agnostic machinery base with **no sink
of its own**; every concrete backend (console, broker) is registered on top of
it as a peer. The console sink now lives in `ConsoleBackend`, and
`AsyncBaseHandler` is a thin subclass that registers it. This separation is the
central structural decision of the package.

**Related.** `console_backend.py`, `message_broker_handler.py`, `formatter.py`.

---

## 2. `stop_logging` — generic drain via per-backend hooks

**Location.** `AsyncLoggingHandler.stop_logging`, `async_logging_handler.py:209-241`.

**What it appears to do.** Clears both events, then iterates the registered
`drain` hooks in **reverse registration order**, awaiting each with the shared
timeout and collecting `BackendDrainResult`s. Finally gathers worker tasks and
closes the handler.

**Why significant.** Shutdown is now generic — no queue-name special-casing.
Each backend controls its own drain; the console backend (registered first)
drains last so it can observe every other backend's outcome and synthesize
status records. The reverse-registration ordering and the timeout handling are
subtle and worth confirming for new backends.

**Related.** `data-flow.md` Flow 4; `console_backend.py`; `message_broker_handler.py`.

---

## 3. Worker coroutine lifecycle — created once, not restartable

**Location.** `AsyncLoggingHandler.__init__` (`async_logging_handler.py:120-125`),
`start_logging` (`async_logging_handler.py:170`), `stop_logging`.

**What it appears to do.** Worker *coroutines* are created in `__init__` (by
each backend's registration) and appended to `log_workers`. `start_logging`
schedules them with `asyncio.create_task`. `stop_logging` gathers the tasks.

**Why significant.** Because coroutines are consumed when scheduled, a second
`start_logging()` after `stop_logging()` would schedule already-consumed
coroutines. Whether a handler is restartable is `UNKNOWN` and untested. The
separation between "worker coroutine" and "worker task" is a subtle ownership
boundary worth clarifying.

**Related.** `lifecycle.md`; `tests/test_basic_handler.py`.

---

## 4. `emit` — synchronous puts with error reporting

**Location.** `AsyncLoggingHandler.emit`, `async_logging_handler.py:172-207`.

**What it appears to do.** For each registered queue, calls
`queue.put_nowait(record)` synchronously. A failed put is reported through the
error channel (`_report_error`), not swallowed.

**Why significant.** `emit` is the hot path for every log record. It must be
called from the event-loop thread (off-loop raises `RuntimeError`). The
error-handling policy routes failures to the configured `error_handler` or the
`scieetex.logging` module logger.

**Related.** `data-flow.md`; `tests/test_basic_handler.py`.

---

## 5. Unbounded queues / no backpressure

**Location.** `asyncio.Queue()` construction in `console_backend.py:74` and
`message_broker_handler.py:61` (no `maxsize`).

**What it appears to do.** All backend queues are unbounded.

**Why significant.** Under sustained high-volume logging where consumers lag,
queues grow without bound (memory pressure). There is no backpressure or
drop policy. The `QueueFull` handling in `emit` suggests a bounded-queue intent
that is not realized.

**Related.** `emit` (hotspot 4); docs claim "high-throughput" support.

---

## 6. Console as a peer backend vs. a shared sink

**Location.** `console_backend.py` (queue + worker + drain); `basic_handler.py`
(registers the console backend); `message_broker_handler.py`.

**What it appears to do.** Every handler instance that has `stdout_enable=True`
registers its own `ConsoleBackend` (queue + worker). Broker handlers therefore
run console + broker workers independently. `stop_logging` drains backends in
reverse registration order, so the console (registered first) drains last and
reports the other backends' outcomes.

**Why significant.** The console backend is both a standalone backend
(`AsyncBaseHandler`) and an auxiliary output attached to broker handlers. This
dual role is now explicit: console is a peer backend registered the same way a
broker backend is, rather than a privileged sink baked into the base machinery.

**Related.** `overview.md`, `data-flow.md` Flow 3.

---

## 7. Duplicated connect/disconnect/send_message contract

**Location.** `message_broker_handler.py` (abstract methods),
`redis_handler.py`, `valkey_handler.py`.

**What it appears to do.** `AsyncBrokerHandler` is `abc.ABC`; `connect`/
`disconnect`/`send_message` are `@abc.abstractmethod`. Redis and Valkey each
implement them. The two concrete implementations differ in signature details
(e.g. Valkey passes `record.items()` to `xadd`, Redis passes `record`).

**Why significant.** The extension contract is now enforced by the type system
(`abc`), but the Redis/Valkey `xadd` argument difference (`dict` vs
`dict.items()`) is a subtle asymmetry.

**Related.** `components.md`; `docs/advanced.md` (custom backend examples).

---

## 8. Optional-dependency guard duplication

**Location.** `redis_handler.py:3-9`, `valkey_handler.py:3-9`, and the guarded
imports in `__init__.py:112-123`.

**What it appears to do.** Each backend module hard-imports its client and
raises a descriptive `ImportError`; `__init__.py` wraps each import in
try/except `ImportError` to conditionally expose the class.

**Why significant.** The guard logic is split across two layers (module-level
raise + package-level catch). Importing `redis_handler` directly (as tests and
examples do) raises if `redis` is absent, while importing from the package root
silently omits the class. This dual behavior is a subtle seam in the
optional-dependency design.

**Related.** `dependencies.md`; `tests/test_redis_handler.py`,
`tests/test_valkey_handler.py` (import from submodules directly).

---

## 9. Version skew between tox and packaging config

**Location.** `tox.ini` (`valkey-glide~=2.2.0` in `type` and default envs,
lines 29, 37) vs `pyproject.toml` (`valkey-glide~=2.5.0`, line 22).

**What it appears to do.** tox installs a different `valkey-glide` version
range than the package declares.

**Why significant.** CI/tox environments may exercise a different client
version than what end users install, potentially masking or introducing
incompatibilities. A config-level inconsistency worth reconciling.

**Related.** `pyproject.toml`, `tox.ini`.

---

## 10. CI asymmetry: Redis provisioned, Valkey not

**Location.** `.github/workflows/python-package.yml` (Redis service container,
lines 17-30).

**What it appears to do.** CI runs the full pytest suite with a Redis service
but no Valkey service.

**Why significant.** `tests/test_valkey_handler.py` requires a live Valkey
server and is not skipped when absent, so it would fail in CI unless Valkey is
otherwise available. The test suite's external-server dependency is not
uniformly provisioned.

**Related.** `tests/test_valkey_handler.py`, `tests/test_redis_handler.py`.

---

## 11. Formatter mutates the shared `LogRecord`

**Location.** `ScietexFormatter.format`, `formatter.py:89-109`.

**What it appears to do.** `format` sets `record.worker_name` and overwrites
`record.levelname` on the record object before delegating to the parent
formatter.

**Why significant.** `LogRecord`s are shared objects. Because `emit` fans one
record to multiple queues, and each backend's worker formats the same record,
the mutation is idempotent here (same formatter). But if a handler had multiple
formatters or the record were reused across handlers with different
service/worker identities, mutation could leak. The broker worker reads
`record.worker_name` / `record.levelname` that the formatter set — an implicit
ordering dependency between formatting and dict-building.

**Related.** `data-flow.md` Flow 2; `message_broker_handler.py:127-138`.

---

## 12. `AsyncBrokerHandler._worker` — connection + drain coupling

**Location.** `message_broker_handler.py:104-148`.

**What it appears to do.** The worker calls `connect()` once at start, then
loops draining the queue and calling `send_message`, then `disconnect()` at
exit. If `connect()` fails or the client is None, records are still dequeued
and `task_done()` called (dropped silently).

**Why significant.** Connection lifecycle, message dispatch, and queue draining
are interleaved in one loop. Failure modes (connect failure, send failure,
client None) are not surfaced — records are silently dropped. The worker's
`task_done()` is called even when `send_message` was skipped (client None),
so the queue drains without the message being delivered.

**Related.** `lifecycle.md`; `redis_handler.py`, `valkey_handler.py`.
