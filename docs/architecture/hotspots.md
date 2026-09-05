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

## 3. Worker lifecycle — restartable via worker factories

**Location.** `AsyncLoggingHandler.__init__` (`async_logging_handler.py:126`),
`start_logging` (`async_logging_handler.py:162`), `stop_logging`.

**What it appears to do.** Worker *factories* (zero-argument callables returning
a fresh coroutine) are registered in `__init__` by each backend and appended to
`log_worker_factories`. `start_logging` invokes each factory and schedules the
resulting coroutine with `asyncio.create_task`. `stop_logging` gathers the
tasks and resets `log_workers_tasks`.

**Why significant.** Because workers are factories, the handler is restartable:
each `start_logging` schedules fresh tasks from a clean queue. `start_logging`
raises `RuntimeError` if already running; `stop_logging` is idempotent and no
longer calls `close()`. Restart is only supported on the same event loop
(events/queues are loop-bound). The factory/task separation is the ownership
boundary that makes the lifecycle restartable.

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

## 5. Bounded queues with drop + report overflow

**Location.** `asyncio.Queue(maxsize=...)` construction in `console_backend.py:77`
and `message_broker_handler.py:62`; the explicit `except asyncio.QueueFull`
branch in `emit` (`async_logging_handler.py:232`).

**What it appears to do.** Every backend queue is bounded by `queue_maxsize`
(default 10000), set on `AsyncLoggingHandler`/`AsyncBaseHandler` and stored as
`self.queue_maxsize`. `ConsoleBackend` builds `asyncio.Queue(maxsize=maxsize)`;
`AsyncBrokerHandler` builds `asyncio.Queue(maxsize=self.queue_maxsize)`.

**Why significant.** The overflow policy is **drop + report**: when a backend
queue is full at emit time, `emit` drops the record and routes an
`asyncio.QueueFull` to the error channel (`_report_error` → `error_handler`
callback or module logger). `emit` never blocks, so the producer stays
non-blocking under sustained overload. This resolves the earlier unbounded
buffering concern: under overload, records now drop + report instead of growing
memory without bound. `ConsoleBackend.drain` uses `put_nowait` for its synthetic
shutdown-status records, dropping them if the console queue is full so shutdown
never deadlocks on a bounded queue.

**Related.** `emit` (hotspot 4); `data-flow.md` cross-cutting notes.

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
(`abc`). The Redis/Valkey `xadd` argument difference (`dict` vs `dict.items()`)
is an **intentional, documented adapter difference**: the abstract
`send_message` contract states `record` is a serializable `dict[str, str]` of
`{level, message, name, time}`, and each concrete adapter translates it to the
argument shape its client expects. No uniform client wrapper was added.

**Related.** `components.md`; `docs/advanced.md` (custom backend examples).

---

## 8. Optional-dependency guard duplication

**Location.** `redis_handler.py:3-9`, `valkey_handler.py:3-9`, and the guarded
imports in `__init__.py:112-123`.

**What it appears to do.** Each backend module hard-imports its client and
raises a descriptive `ImportError`; `__init__.py` wraps each import in
try/except `ImportError` to conditionally expose the class.

**Why significant.** **Resolved.** The two-layer guard structure (module-level
raise + package-level catch) is intentional and retained, but the error-message
text is now centralized in the shared `optional_dependency_error(module_name,
extra)` helper in `config.py`, which both backend modules call when raising
(`raise ImportError(optional_dependency_error(...)) from e`). The layers can no
longer drift in message text.

**Related.** `dependencies.md`; `tests/test_redis_handler.py`,
`tests/test_valkey_handler.py` (import from submodules directly).

---

## 9. Version skew between tox and packaging config

**Location.** `tox.ini` (`valkey-glide~=2.5.0` in `type` and default envs,
lines 29, 37) and `pyproject.toml` (`valkey-glide~=2.5.0`, line 22).

**What it appears to do.** tox and the package declare the same `valkey-glide`
version range.

**Why significant.** **Resolved.** `tox.ini` was aligned from `~=2.2.0` to
`~=2.5.0`, matching `pyproject.toml`. CI/tox now exercises the same client
version range that end users install.

**Related.** `pyproject.toml`, `tox.ini`.

---

## 10. CI asymmetry: Redis provisioned, Valkey not

**Location.** `.github/workflows/python-package.yml` (Redis service container,
lines 17-30).

**What it appears to do.** CI runs the full pytest suite with a Redis service
but no Valkey service.

**Why significant.** **Resolved.** The Valkey end-to-end test in
`tests/test_valkey_handler.py` now carries a connectivity-probe skip guard
(`@pytest.mark.skipif` via a `_valkey_server_reachable()` socket probe), so it
skips cleanly when no Valkey server is reachable instead of failing. Valkey is
still not provisioned in CI — the skip-guard approach was chosen over CI
provisioning.

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
