# Hotspots

Areas that deserve deeper architectural investigation. This is **not** a
critique or a refactor proposal — it flags structurally significant locations
for the later deep review. Each entry gives location, what the code appears to
do, why it is significant, and related files.

---

## 1. `AsyncBaseHandler` — multi-responsibility core

**Location.** `src/scietex/logging/basic_handler.py` (`AsyncBaseHandler`, 262-line module).

**What it appears to do.** One class owns: the stdlib `logging.Handler`
integration (`emit`), the async queue/event machinery, worker task lifecycle,
the console backend worker, put-task bookkeeping, and the entire graceful
shutdown sequence.

**Why significant.** It is simultaneously the base class for all backends and
the concrete console backend. Backend-agnostic machinery and a specific
backend (console) live in the same class. `stop_logging` also contains
backend-specific logic (it special-cases the `"console"` queue and injects
synthetic status records). This conflation is the central structural decision
of the package.

**Related.** `message_broker_handler.py`, `formatter.py`.

---

## 2. `stop_logging` — complex, backend-aware shutdown

**Location.** `AsyncBaseHandler.stop_logging`, `basic_handler.py:152-235`.

**What it appears to do.** Clears events, drains put tasks, then iterates
`log_queues` skipping `"console"`, waiting on each non-console queue's
`join()` with a timeout, and synthesizing INFO/ERROR `LogRecord`s into the
console queue to report each backend's drain outcome. Finally drains the
console queue and gathers workers.

**Why significant.** The shutdown logic is coupled to the queue *names*
(`"console"` special-cased) and reaches into per-backend behavior. It mixes
control-flow reporting (synthetic records) onto the same data path as user
logs. The ordering (non-console queues first, console last) and the timeout
handling are subtle and hard to extend for new backends.

**Related.** `data-flow.md` Flow 4; `message_broker_handler.py`.

---

## 3. Worker coroutine lifecycle — created once, not restartable

**Location.** `AsyncBaseHandler.__init__` (`basic_handler.py:89-92`),
`start_logging` (`basic_handler.py:113`), `stop_logging`.

**What it appears to do.** Worker *coroutines* are created in `__init__` and
appended to `log_workers`. `start_logging` schedules them with
`asyncio.create_task`. `stop_logging` gathers the tasks.

**Why significant.** Because coroutines are consumed when scheduled, a second
`start_logging()` after `stop_logging()` would schedule already-consumed
coroutines. Whether a handler is restartable is `UNKNOWN` and untested. The
separation between "worker coroutine" and "worker task" is a subtle ownership
boundary worth clarifying.

**Related.** `lifecycle.md`; `tests/test_basic_handler.py`.

---

## 4. `emit` — fire-and-forget put tasks with silent error swallowing

**Location.** `AsyncBaseHandler.emit`, `basic_handler.py:115-150`.

**What it appears to do.** For each queue, creates an `asyncio.Task` for
`queue.put(record)`, tracks it, and prunes completed tasks at a threshold.
Catches `QueueFull`, `InvalidStateError`, and a bare `Exception` — all with
empty `pass` bodies.

**Why significant.** The put tasks are unbounded in number between cleanups
(threshold default 100). The bare `except Exception: pass` swallows all errors
silently. `QueueFull` is caught but the queues are unbounded (no `maxsize`), so
that branch is effectively dead. This is the hot path for every log record and
the error-handling policy here is opaque.

**Related.** `data-flow.md`; `tests/test_basic_handler.py` (cleanup tests).

---

## 5. Unbounded queues / no backpressure

**Location.** `asyncio.Queue()` construction in `basic_handler.py:91` and
`message_broker_handler.py:56` (no `maxsize`).

**What it appears to do.** All backend queues are unbounded.

**Why significant.** Under sustained high-volume logging where consumers lag,
queues grow without bound (memory pressure). There is no backpressure or
drop policy. The `QueueFull` handling in `emit` suggests a bounded-queue intent
that is not realized.

**Related.** `emit` (hotspot 4); docs claim "high-throughput" support.

---

## 6. Console as a peer backend vs. a shared sink

**Location.** `basic_handler.py` (console queue + worker); `stop_logging`
special-casing; `message_broker_handler.py`.

**What it appears to do.** Every handler instance that has `stdout_enable=True`
runs its own console worker and console queue. Broker handlers therefore run
console + broker workers independently. `stop_logging` treats console as
"last" and injects broker status into it.

**Why significant.** The console backend is both a standalone backend
(`AsyncBaseHandler`) and an auxiliary output attached to broker handlers. This
dual role drives the queue-name special-casing and the synthetic-record
reporting. The relationship between "console" and "broker" backends is the
least explicit part of the model.

**Related.** `overview.md`, `data-flow.md` Flow 3.

---

## 7. Duplicated connect/disconnect/send_message contract

**Location.** `message_broker_handler.py` (abstract no-op methods),
`redis_handler.py`, `valkey_handler.py`.

**What it appears to do.** `AsyncBrokerHandler.connect`/`disconnect`/
`send_message` are empty base methods (documented as "redefine in subclass").
Redis and Valkey each implement them. There is no abstract-method enforcement
(`abc`), no shared validation, and the two concrete implementations differ in
signature details (e.g. Valkey passes `record.items()` to `xadd`, Redis passes
`record`).

**Why significant.** The extension contract is enforced only by convention and
documentation, not by the type system. A subclass that forgets to implement
`send_message` would silently no-op. The Redis/Valkey `xadd` argument
difference (`dict` vs `dict.items()`) is a subtle asymmetry.

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

**Related.** `data-flow.md` Flow 2; `message_broker_handler.py:109-124`.

---

## 12. `AsyncBrokerHandler._worker` — connection + drain coupling

**Location.** `message_broker_handler.py:93-130`.

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
