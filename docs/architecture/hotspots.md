# Hotspots

Areas that deserve deeper architectural investigation. This is **not** a
critique or a refactor proposal — it flags structurally significant locations
for the later deep review. Each entry gives location, what the code appears to
do, why it is significant, and related files.

---

## 1. `AsyncLoggingHandler` — the shared machinery core

**Location.** `src/scietex/logging/async_logging_handler.py` (`AsyncLoggingHandler`, 316-line module).

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

## 2. `stop_logging` — coordinator-owned drain via per-backend hooks

**Location.** `AsyncLoggingHandler.stop_logging`, `async_logging_handler.py:301-361`.

**What it appears to do.** Clears the accept event, then iterates the registered
`drain` hooks in **registration order**, awaiting each with the shared timeout
and collecting the `BackendDrainResult` each hook returns. After every drain
concludes, it invokes each registered status reporter with the collected
results. Finally it clears the running event, gathers worker tasks, and resets
`log_workers_tasks`.

**Why significant.** Shutdown is now generic — no queue-name special-casing.
Each backend controls its own drain and returns its own result; the coordinator
owns result collection. The console backend is a *status reporter* (registered
via `register_status_reporter`), invoked after all drains with the full results
list, so it synthesizes status records without depending on drain registration
order. The timeout handling and the drain-then-report sequence are subtle and
worth confirming for new backends.

**Related.** `data-flow.md` Flow 4; `console_backend.py`; `message_broker_handler.py`.

---

## 3. Worker lifecycle — restartable via worker factories

**Location.** `AsyncLoggingHandler.__init__` (`async_logging_handler.py:111`),
`start_logging` (`async_logging_handler.py:234`), `stop_logging`.

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

**Location.** `AsyncLoggingHandler.emit`, `async_logging_handler.py:259-299`.

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
and `message_broker_handler.py:81`; the explicit `except asyncio.QueueFull`
branch in `emit` (`async_logging_handler.py:242`).

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
run console + broker workers independently. `stop_logging` drains each backend
(collecting its returned result) and then invokes the console's `report_status`
as a status reporter with the full results list, so the console reports the
other backends' outcomes as a post-drain observer rather than by drain order.

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

**Location.** `redis_handler.py:5-8`, `valkey_handler.py:5-8`, and the guarded
imports in `__init__.py:116-127`.

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

## 11. Formatter copies the record; broker dict built independently

**Location.** `ScietexFormatter.format`, `formatter.py:90-113`.

**What it appears to do.** `format` first copies the record
(`record = copy.copy(record)` at `formatter.py:104`), then sets
`record.worker_name` and overwrites `record.levelname` on the **copy** before
delegating to the parent formatter. The caller's shared `LogRecord` is never
mutated.

**Why significant.** `LogRecord`s are shared objects: `emit` fans one record to
multiple queues, and each backend's worker formats the same record. Because
`format` mutates only a copy, no mutation leaks to the caller or to other
backends. The broker worker additionally computes its dict fields
**independently** — `level = level_abbreviation(record.levelno)`,
`name = f"{self.config.service_name}:{self.config.worker_id}"`, and
`time = datetime.fromtimestamp(record.created, timezone.utc).isoformat()`
(`message_broker_handler.py:172-178`) — from config and the record rather than
from formatter-mutated attributes, so the broker wire format is invariant under
`setFormatter` and there is **no implicit ordering dependency** between
formatting and dict-building.

**Related.** `data-flow.md` Flow 2; `message_broker_handler.py:172-178`.

---

## 12. `AsyncBrokerHandler._worker` — connection + drain coupling

**Location.** `message_broker_handler.py:131-197`.

**What it appears to do.** The worker calls `connect()` once at start, then
loops draining the queue and calling `send_message`, then `disconnect()` at
exit. On a `connect()` failure it reports via the error channel, sleeps ~1s,
and retries **without dequeuing** the record. On a `send_message()` failure it
reports via the error channel, tears the client down (`disconnect()`, with a
`self.client = None` fallback if that raises) so the next iteration reconnects,
and acknowledges the record via `task_done()` in a `finally`.

**Why significant.** Connection lifecycle, message dispatch, and queue draining
are interleaved in one loop. Failure modes are surfaced through the error
channel rather than silently dropping records: a failed `connect()` is retried
(no record is dequeued), and a failed `send_message()` is reported, the dead
client is released so the worker re-enters `connect()`, and the dequeued record
is acked exactly once (`task_done()` in a `finally`) so `queue.join()` in the
drain can complete. The whole connect-retry + drain loop is wrapped in a
`finally` that runs `disconnect()` and resets `self.client = None` on both
normal exit and cancellation, so a cancelled worker never leaks the client.

**Related.** `lifecycle.md`; `redis_handler.py`, `valkey_handler.py`.
