# P3 Polish Fixes (AR-020..AR-036) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the 15 remaining Priority-3 architecture-review findings — AR-020, AR-021, AR-022, AR-024, AR-025, AR-026, AR-027, AR-028, AR-029, AR-030, AR-031, AR-033, AR-034, AR-035, AR-036 — as small, independently verifiable hardening changes that keep the full suite green.

**Architecture:** No redesign. Each finding is a localized hardening change to the existing handler/backend/config/test/doc surface. Findings that touch the same file or region are grouped into task clusters so each task is independently testable and reviewable. Four findings require a deliberate behavior decision (AR-020 clear-vs-replay, AR-022 backoff params, AR-024 formatter kwarg, AR-025 config-input unification, AR-035 public-surface) — each is flagged for sign-off before its task runs.

**Tech Stack:** Python >=3.10, asyncio, stdlib `dataclasses`/`typing`, pytest-asyncio, ruff, ty. No new dependencies.

**Spec:** `docs/reviews/architecture/2026-09-05.md` findings AR-020 (lines 348-359), AR-021 (361-373), AR-022 (375-386), AR-024 (414-425), AR-025 (427-439), AR-026 (441-453), AR-027 (455-468), AR-028 (470-482), AR-029 (484-497), AR-030 (501-512), AR-031 (514-522), AR-033 (545-553), AR-034 (555-566), AR-035 (568-578), AR-036 (580-589), plus the P3 recommendation (lines 651-663). Executors read both this plan and the review.

## Global Constraints

- Working tree must stay green after each task: `uv run pytest -q`, `uv run ruff check .`, `uv run ty check src/scietex/logging/`.
- Do NOT modify files outside the exact paths listed per task. P1/P2 findings (AR-013..AR-019, AR-023, AR-032) are **already resolved** — do not re-touch their resolved code except where a P3 task legitimately extends it (e.g. AR-021 adds an error channel to the console worker; AR-022 changes the connect-retry sleep that AR-014/AR-015 already hardened).
- Preserve the strict one-way acyclic import graph. `config.py` and `formatter.py` import only stdlib. No module may import a handler module from `config.py` (cycle). `config.py` must never import `formatter.py` and vice-versa (both are stdlib-only leaves; AR-026/AR-036 must not create a cycle between them).
- **Public constructor API stays backward-compatible** unless a finding explicitly requires a decision (AR-024 formatter kwarg, AR-025 config-input unification, AR-035 public surface). Every existing call site in `examples/`, `docs/`, `tests/`, and `__init__.py` docstrings must keep working unchanged unless the task explicitly changes it.
- No emoji in code/comments. Comments explain WHY, not WHAT. No commented-out code. No `@ts-ignore`-style suppressions.
- Every public function/method needs at least one caller before commit; no dead code.
- Commit style follows repo history: `fix: ...`, `refactor: ...`, `test: ...`, `docs: ...` conventional prefixes (see `git log --oneline`).
- The working tree is CLEAN at commit 5461275. Stage only the files each task lists; never `git add -A` or `git add <directory>`.

---

## Decisions Requiring Sign-off (read before Task 1)

These five findings each require a deliberate behavior choice. The plan below recommends a default and proceeds on it; confirm each before its task runs, or the executor must adjust that task only.

### D1 — AR-020: queue clear-vs-replay semantics (RECOMMENDED: clear on stop)

The class docstring claims "each start schedules fresh tasks from a clean queue" (`async_logging_handler.py:60-63`), but `register_backend` stores the queue once (`:214`) and `start_logging` only recreates the *tasks* (`:255-257`). If a prior cycle timed out with records still queued, those records replay next cycle → duplicate broker delivery.

**Recommended:** **Clear undelivered records on stop** so the docstring is true. Rationale: a logging library must not silently duplicate broker deliveries across a restart; the drop-and-report overflow policy already establishes that undelivered records are dropped with visibility, not preserved. `stop_logging` already reports a `DrainStatus.TIMEOUT` for any backend whose queue did not drain — that is the visibility for the drop. Clearing on stop makes restart semantics deterministic and matches the documented "clean queue" contract.

**Alternative (document replay):** keep queues, edit the docstring to say undrained records replay. Simpler but preserves the duplicate-delivery hazard and contradicts the existing "clean queue" wording.

**Decision needed:** confirm clear-on-stop before Task 1. If replay is chosen, Task 1 becomes a doc-only change.

### D2 — AR-022: connect-retry backoff parameters (RECOMMENDED: capped exponential backoff + jitter)

The connect-retry loop sleeps a fixed `1.0`s forever (`message_broker_handler.py:154-160`), spamming the error channel ~3600×/hr during an outage.

**Recommended:** capped exponential backoff with jitter: start at `0.5`s, double each consecutive failure, cap at `30`s, add ±20% uniform jitter, and reset to the base delay on a successful connect. No max-attempts / circuit-breaker (a logging handler must keep trying to reconnect for the handler's lifetime; a permanent outage is surfaced once per backoff tick, not per second). The backoff state is per-worker and resets each `start_logging` cycle because workers are fresh coroutines.

**Decision needed:** confirm the base/cap/jitter values before Task 4. These are tunable constants; the plan uses `0.5`/`30`/`0.2`.

### D3 — AR-024: `formatter=` construction kwarg (RECOMMENDED: add optional kwarg)

`AsyncLoggingHandler.__init__` hard-codes `ScietexFormatter` (`async_logging_handler.py:162-164`); substitution is only possible post-hoc via `setFormatter`.

**Recommended:** add an optional keyword-only `formatter: logging.Formatter | None = None` to `AsyncLoggingHandler.__init__` (and thread it through `AsyncBaseHandler`/`AsyncBrokerHandler`/`AsyncRedisHandler`/`AsyncValkeyHandler`). When `None`, construct the default `ScietexFormatter` as today; when provided, use it. This is safe because AR-018 already decoupled broker serialization from the formatter, so a plain formatter no longer changes the broker wire format. `setFormatter` continues to work post-hoc.

**Decision needed:** confirm the kwarg name (`formatter`) and that it is keyword-only before Task 6.

### D4 — AR-025: backend config-input unification (RECOMMENDED: accept dict for both, translate inside `connect()`)

Redis takes `redis_config: dict | None` (`redis_handler.py:43`); Valkey takes `valkey_config: GlideClientConfiguration | None` (`valkey_handler.py:43`), leaking a third-party type into the public constructor.

**Recommended:** make **both** accept a plain `dict` (Valkey accepts `dict | None` describing `{"addresses": [{"host": ..., "port": ...}, ...]}` or a single `{"host", "port"}`), and translate to the vendor `GlideClientConfiguration` *inside* `connect()`. Keep accepting a `GlideClientConfiguration` for Valkey as a backward-compatible alternative (duck-typed: if it has `.addresses`, treat it as a config object). This gives one package-level config story (dict) while preserving existing callers.

**Decision needed:** confirm the Valkey dict schema before Task 7. The plan uses `{"addresses": [{"host": "localhost", "port": 6379}]}` with a single-node shorthand `{"host": "localhost", "port": 6379}`.

### D5 — AR-035: public-surface decision (RECOMMENDED: export config types, keep ConsoleBackend, align AGENTS.md)

`__all__` exports `ConsoleBackend` (`__init__.py:108-114`) but not `LoggingConfig`/`RedisConfig`/`ValkeyConfig`; `AGENTS.md`'s Public API section omits `AsyncLoggingHandler` and `ConsoleBackend`.

**Recommended:** **export the three config types** (`LoggingConfig`, `RedisConfig`, `ValkeyConfig`) since the config seam is kept (AR-016/017/023 resolved it as the single source of truth), **keep `ConsoleBackend` exported** (it is a documented peer-backend pattern and `docs/configuration.md` references it), and **align `AGENTS.md`** to list `AsyncLoggingHandler` and `ConsoleBackend` alongside the config types. This makes `__all__` match the real public surface and the docs.

**Decision needed:** confirm before Task 10. If instead `ConsoleBackend` should be demoted to internal, Task 10 changes to remove it from `__all__` and update the docs that reference it.

---

## File Structure

Files touched across the task clusters:

| File | Responsibility | Findings |
|---|---|---|
| `src/scietex/logging/async_logging_handler.py` | Machinery base + shutdown | AR-020 (clear queues on stop), AR-024 (formatter kwarg), AR-028 (dup-name guard), AR-031 (error-handler fallback) |
| `src/scietex/logging/console_backend.py` | Console sink | AR-021 (error channel), AR-030 (dynamic formatter read) |
| `src/scietex/logging/basic_handler.py` | Console handler | AR-030 (drop manual formatter re-sync), AR-024 (thread formatter kwarg) |
| `src/scietex/logging/message_broker_handler.py` | Broker base + worker | AR-022 (backoff), AR-026 (import `level_abbreviation` from new home) |
| `src/scietex/logging/redis_handler.py` | Redis adapter | AR-033 (connect leak), AR-034 (send_message precondition), AR-025 (dict config), AR-024 (formatter kwarg) |
| `src/scietex/logging/valkey_handler.py` | Valkey adapter | AR-034 (send_message precondition), AR-025 (dict config), AR-024 (formatter kwarg) |
| `src/scietex/logging/config.py` | Config leaf | AR-026 (host `level_abbreviation`), AR-036 (host `optional_dependency_error`) |
| `src/scietex/logging/formatter.py` | Formatter | AR-026 (re-export `level_abbreviation` for back-compat) |
| `src/scietex/logging/__init__.py` | Public API | AR-027 (tighten ImportError guard), AR-035 (export config types) |
| `tests/test_async_logging_handler.py` | Machinery tests | AR-020, AR-024, AR-028, AR-031 |
| `tests/test_console_backend.py` | Console tests | AR-021, AR-030 |
| `tests/test_message_broker_handler.py` | Broker tests | AR-022, AR-026 |
| `tests/test_redis_handler.py` | Redis tests | AR-029 (skip guard), AR-033, AR-034, AR-025 |
| `tests/test_valkey_handler.py` | Valkey tests | AR-034, AR-025 |
| `tests/test_config.py` | Config tests | AR-026, AR-036 |
| `tests/test_formatter.py` | Formatter tests | AR-026 (re-export still works) |
| `tests/test_basic_handler.py` | Console handler tests | AR-030, AR-024 |
| `AGENTS.md` | Repo guide | AR-035 (Public API section) |
| `docs/configuration.md` | Config doc | AR-024, AR-025, AR-035 |
| `docs/backends.md` | Backend doc | AR-025 |
| `docs/architecture/components.md` | Component doc | AR-026, AR-036, AR-035 |
| `docs/architecture/structure.md` | Structure doc | AR-026, AR-036 |
| `docs/architecture/dependencies.md` | Dependency doc | AR-026, AR-036 |
| `docs/architecture/hotspots.md` | Hotspots doc | AR-026, AR-036 |
| `docs/architecture/overview.md` | Overview doc | AR-026 |
| `docs/architecture/data-flow.md` | Data-flow doc | AR-026 |
| `docs/reviews/architecture/2026-09-05.md` | Review record | Append Resolution notes for each resolved finding |

---

## Task Cluster A: Queue lifecycle & machinery hardening (AR-020, AR-028, AR-031)

These three findings all live in `async_logging_handler.py` and are behavior-preserving except AR-020's clear-on-stop decision (D1).

### Task 1: AR-020 — clear undelivered records on stop

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:301-361` (`stop_logging`)
- Test: `tests/test_async_logging_handler.py`

**Interfaces:**
- Consumes: existing `stop_logging(timeout=5.0)`, `self.log_queues`, `self.log_workers_tasks`.
- Produces: after `stop_logging` returns, every backend queue is empty (undelivered records dropped). No signature change.

**Background (why):** The class docstring promises "each start schedules fresh tasks from a clean queue" (`async_logging_handler.py:60-63`), but queues persist across restarts. If a prior cycle timed out with records still queued, they replay next cycle → duplicate broker delivery. Decision D1 (clear-on-stop) makes the docstring true.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_async_logging_handler.py`:

```python
@pytest.mark.asyncio
async def test_stop_logging_clears_undrained_records():
    """Records left in a queue after a timed-out drain are dropped, not replayed."""
    handler = BareHandler(service_name="TestService", worker_id=1)
    handler.register_backend("b", asyncio.Queue(), handler._noop_worker, handler._drain_a)
    # Simulate a prior cycle that timed out with a record still queued.
    handler.log_queues["b"].put_nowait(_make_record("stale"))
    await handler.stop_logging(timeout=0.01)
    assert handler.log_queues["b"].empty()
```

Note: `BareHandler` and `_make_record` already exist in this test file (`test_async_logging_handler.py:11-13`); add a `_make_record` helper if not present, and a `_noop_worker`/`_drain_a` pair on the handler if the existing `ReportingHandler` inner class is not reusable. If the file lacks these helpers, define them locally in the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_stop_logging_clears_undrained_records -v`
Expected: FAIL — the stale record is still in the queue after `stop_logging`.

- [ ] **Step 3: Implement clear-on-stop**

In `stop_logging`, after the workers have been gathered/cancelled and `self.log_workers_tasks = []` (`async_logging_handler.py:361`), clear every backend queue:

```python
        self.log_workers_tasks = []

        # Drop any records that a timed-out drain left behind so a restart starts
        # from a clean queue (matching the class docstring). A TIMEOUT drain result
        # already surfaced the drop via the status reporters; replaying stale
        # records next cycle would risk duplicate broker delivery.
        for queue in self.log_queues.values():
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                queue.task_done()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_async_logging_handler.py::test_stop_logging_clears_undrained_records -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (existing restart tests in `test_restartable_lifecycle.py` and `test_queue_bounds.py` already assert empty queues after stop, so they remain green).

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_async_logging_handler.py
git commit -m "fix: clear undrained records on stop so restarts start clean (AR-020)"
```

### Task 2: AR-028 — duplicate-name guard in `register_backend`

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:188-217` (`register_backend`)
- Test: `tests/test_async_logging_handler.py`

**Interfaces:**
- Consumes: existing `register_backend(name, queue, worker, drain=None)`.
- Produces: `register_backend` raises `ValueError` when a backend name is already registered. No signature change.

**Background (why):** `register_backend` overwrites `self.log_queues[name]` on a duplicate but still appends the worker factory and drain hook (`async_logging_handler.py:214-217`), desynchronizing `log_queues` from the parallel lists. In-package the fixed set (`"console"`, `"redis"`, `"valkey"`) is safe; the risk is third-party `AsyncBrokerHandler` subclasses (the documented extension point).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_async_logging_handler.py`:

```python
def test_register_backend_rejects_duplicate_name():
    """Registering two backends under one name raises instead of silently mis-wiring."""
    handler = BareHandler(service_name="TestService", worker_id=1)
    handler.register_backend("dup", asyncio.Queue(), handler._noop_worker)
    with pytest.raises(ValueError):
        handler.register_backend("dup", asyncio.Queue(), handler._noop_worker)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_register_backend_rejects_duplicate_name -v`
Expected: FAIL — no exception raised.

- [ ] **Step 3: Implement the guard**

At the top of `register_backend` (`async_logging_handler.py:214`), before the assignments:

```python
        if name in self.log_queues:
            raise ValueError(f"backend {name!r} is already registered")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_async_logging_handler.py::test_register_backend_rejects_duplicate_name -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (no in-package code registers a duplicate name).

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_async_logging_handler.py
git commit -m "fix: reject duplicate backend names in register_backend (AR-028)"
```

### Task 3: AR-031 — `_report_error` falls back to the module logger when the callback raises

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:363-387` (`_report_error`)
- Test: `tests/test_async_logging_handler.py`

**Interfaces:**
- Consumes: existing `_report_error(record, exc)`.
- Produces: when a configured `error_handler` raises, the error is logged via the `scietex.logging` module logger instead of being silently swallowed. No signature change.

**Background (why):** `_report_error` swallows exceptions from a buggy user `error_handler` (`async_logging_handler.py:375-380`), losing all error telemetry — including the overflow-drop reports, the only visibility into `emit`'s silent drop policy.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_async_logging_handler.py`:

```python
@pytest.mark.asyncio
async def test_report_error_falls_back_to_logger_when_handler_raises(caplog):
    """A buggy error_handler must not swallow the underlying delivery error."""

    def buggy_handler(record, exc):
        raise RuntimeError("buggy error handler")

    handler = BareHandler(service_name="TestService", worker_id=1, error_handler=buggy_handler)
    with caplog.at_level(logging.ERROR, logger="scietex.logging"):
        handler._report_error(None, ConnectionError("delivery failed"))
    assert "delivery failed" in caplog.text
    assert "buggy error handler" in caplog.text
```

Add `import logging` at the top of the test file if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_report_error_falls_back_to_logger_when_handler_raises -v`
Expected: FAIL — nothing is logged when the handler raises.

- [ ] **Step 3: Implement the fallback**

Replace the `except Exception: pass` block in `_report_error` (`async_logging_handler.py:378-380`):

```python
        if self.config.error_handler is not None:
            try:
                self.config.error_handler(record, exc)
            except Exception as handler_exc:
                # A buggy user error_handler must not swallow the underlying
                # delivery error; fall back to the module logger so telemetry
                # (including overflow-drop reports) is never lost.
                _error_logger.error(
                    "%s error_handler raised while reporting a delivery failure: %s",
                    type(self).__name__,
                    handler_exc,
                    exc_info=(type(handler_exc), handler_exc, handler_exc.__traceback__),
                )
                _error_logger.error(
                    "%s failed to deliver a log record: %s",
                    type(self).__name__,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        else:
            _error_logger.error(
                "%s failed to deliver a log record: %s",
                type(self).__name__,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_async_logging_handler.py::test_report_error_falls_back_to_logger_when_handler_raises -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_async_logging_handler.py
git commit -m "fix: fall back to module logger when error_handler raises (AR-031)"
```

---

## Task Cluster B: Console backend symmetry (AR-021, AR-030)

Both findings touch `console_backend.py` and `basic_handler.py`. AR-021 gives the console worker an error channel; AR-030 removes the duplicated formatter state.

### Task 4: AR-021 — console worker error channel

**Files:**
- Modify: `src/scietex/logging/console_backend.py:86-105` (`_worker`)
- Test: `tests/test_console_backend.py`

**Interfaces:**
- Consumes: existing `ConsoleBackend(formatter, running_event, maxsize=10000)`.
- Produces: `ConsoleBackend.__init__` gains an optional `error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None`; `_worker` routes format/write failures through it (or the module logger when None). No other signature change.

**Background (why):** The console worker catches only `asyncio.TimeoutError` (`console_backend.py:104-105`). `self.formatter.format(record)` or `sys.stdout.write` can raise (e.g. `BrokenPipeError` on a closed pipe), terminating the task silently — inconsistent with the broker worker, which routes every failure through `_report_error`. The console is the always-on backend; a broken stdout can crash shutdown or silently kill the console worker.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_console_backend.py`:

```python
class ExplodingFormatter(logging.Formatter):
    """Formatter that raises on format, proving the worker survives format errors."""

    def format(self, record: logging.LogRecord) -> str:
        raise RuntimeError("format exploded")


@pytest.mark.asyncio
async def test_worker_survives_format_error_and_reports():
    """A formatter/write failure is reported, not silently killing the worker."""
    errors = []
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(
        ExplodingFormatter(), running_event, error_handler=lambda record, exc: errors.append(exc)
    )

    worker = asyncio.create_task(backend._worker())
    await backend.queue.put(_make_record("boom"))
    await _wait_for(lambda: bool(errors))
    running_event.clear()
    await asyncio.wait_for(worker, timeout=5)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert backend.queue.empty()
```

Add a `_wait_for` helper to `tests/test_console_backend.py` (copy the pattern from `tests/test_restartable_lifecycle.py:24-30`) if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_console_backend.py::test_worker_survives_format_error_and_reports -v`
Expected: FAIL — the worker task dies with the RuntimeError and `errors` stays empty.

- [ ] **Step 3: Implement the error channel**

Add the `error_handler` parameter to `ConsoleBackend.__init__` (`console_backend.py:67-84`) and store it:

```python
    def __init__(
        self,
        formatter: logging.Formatter | None,
        running_event: asyncio.Event,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        ...
        self.error_handler = error_handler
```

Add a module-level logger and a private `_report_error` method mirroring the broker pattern, then wrap the format/write in `_worker` (`console_backend.py:97-105`):

```python
        while self.running_event.is_set() or not self.queue.empty():
            try:
                record = await asyncio.wait_for(self.queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            try:
                if self.formatter:
                    sys.stdout.write(self.formatter.format(record) + "\n")
                    sys.stdout.flush()
            except Exception as exc:
                # A broken stdout or a buggy formatter must not silently kill the
                # always-on console worker; report it and keep draining so the
                # queue can still be acknowledged and shutdown completes.
                self._report_error(record, exc)
            finally:
                self.queue.task_done()
```

Add the module logger and `_report_error`:

```python
_error_logger = logging.getLogger("scietex.logging")

    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a console delivery error through the configured error channel."""
        if self.error_handler is not None:
            try:
                self.error_handler(record, exc)
            except Exception as handler_exc:
                _error_logger.error(
                    "ConsoleBackend error_handler raised while reporting a delivery failure: %s",
                    handler_exc,
                )
        else:
            _error_logger.error(
                "ConsoleBackend failed to deliver a log record: %s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
```

Note: the `task_done()` must move into a `finally` so a format/write failure still acks the record (mirroring AR-013's broker fix). The existing `test_worker_calls_task_done_for_each_record` (`tests/test_console_backend.py:60-77`) already asserts task_done is called per record and must stay green.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_console_backend.py -v`
Expected: PASS (new test + all existing console tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/console_backend.py tests/test_console_backend.py
git commit -m "fix: give console worker an error channel symmetric with broker (AR-021)"
```

### Task 5: AR-030 — remove duplicated formatter state

**Files:**
- Modify: `src/scietex/logging/console_backend.py:82-84` (`__init__`), `src/scietex/logging/basic_handler.py:95-109` (`setFormatter`)
- Test: `tests/test_basic_handler.py`, `tests/test_console_backend.py`

**Interfaces:**
- Consumes: existing `ConsoleBackend` and `AsyncBaseHandler.setFormatter`.
- Produces: `ConsoleBackend` reads the handler's formatter dynamically instead of capturing a stale copy; `AsyncBaseHandler.setFormatter` no longer manually re-syncs `self._console_backend.formatter`. `ConsoleBackend` keeps a `formatter` attribute for backward compatibility but it is updated by the handler on every `setFormatter` via a shared reference.

**Background (why):** `ConsoleBackend` captures the formatter at construction (`console_backend.py:83`); `AsyncBaseHandler.setFormatter` manually re-syncs `self._console_backend.formatter = fmt` (`basic_handler.py:108-109`). The broker worker reads `self.formatter` dynamically. Two sources of truth for "the active formatter".

**Recommended approach:** standardize on **dynamic reads of the handler's formatter**. Give `ConsoleBackend` a reference to the handler (or a mutable formatter holder) so it always reads the current formatter. The minimal, behavior-preserving change: pass the handler's `setFormatter`-aware formatter through a shared mutable holder.

Concretely, the cleanest minimal change that removes the manual re-sync: have `ConsoleBackend` hold a reference to a small mutable holder object that `AsyncBaseHandler` updates. But to keep the change minimal and avoid a new class, the recommended implementation is:

- `ConsoleBackend.__init__` keeps `self.formatter = formatter` (the initial value).
- `AsyncBaseHandler` passes a **shared mutable list** `[formatter]` as the holder, OR — simpler and matching the broker pattern — `ConsoleBackend` reads the formatter from a callback.

Given the constraint to keep changes minimal and behavior-preserving, the **recommended concrete change** is:

1. In `basic_handler.py`, replace the manual re-sync in `setFormatter` with a call that updates the console backend's formatter through a single shared reference. Since `ConsoleBackend` already stores `self.formatter`, and `AsyncBaseHandler` already holds `self.formatter` (inherited from `logging.Handler`), the duplication is that `_console_backend.formatter` is a *separate* attribute.

The minimal fix that removes the two-source-of-truth hazard without a new class: make `ConsoleBackend` read the formatter from the handler at write time. Change `ConsoleBackend.__init__` to accept the handler (or a `get_formatter` callable) instead of a bare formatter, and have `_worker` call it each iteration.

**Decision (recommended):** Change `ConsoleBackend.__init__` signature to accept a `formatter_provider: Callable[[], logging.Formatter | None]` instead of a bare `formatter`, and have `_worker` call `self.formatter_provider()` per record. `AsyncBaseHandler` passes `lambda: self.formatter`. This removes the manual re-sync in `setFormatter` entirely and makes the console read the handler's formatter dynamically, exactly like the broker.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basic_handler.py`:

```python
@pytest.mark.asyncio
async def test_set_formatter_console_reads_dynamically(capsys):
    """setFormatter must affect console output without a manual backend re-sync."""
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1)
    await handler.start_logging()
    logger = logging.getLogger("TestLogger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info("before")
    handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    logger.info("after")
    await handler.stop_logging()
    captured = capsys.readouterr().out
    assert "INF | after" in captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_basic_handler.py::test_set_formatter_console_reads_dynamically -v`
Expected: FAIL — after `setFormatter`, console output still uses the old format (or the test setup needs the re-sync removed first). Note: this test may pass *before* the change because `setFormatter` currently re-syncs. To make it a true red test, first remove the re-sync in Step 3's code, then confirm the test fails, then implement the dynamic read. Order the steps as: (a) write test, (b) remove the manual re-sync in `basic_handler.py:108-109`, (c) run test → FAIL, (d) implement dynamic read, (e) run test → PASS.

- [ ] **Step 3: Remove the manual re-sync and implement the dynamic read**

In `basic_handler.py`, change the `ConsoleBackend` construction (`basic_handler.py:77-81`) to pass a provider, and simplify `setFormatter`:

```python
        if self.config.stdout_enable:
            self._console_backend = ConsoleBackend(
                lambda: self.formatter,
                self.logging_running_event,
                maxsize=self.config.queue_maxsize,
            )
```

```python
    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        """Set the formatter used to render log records.

        The console backend reads the handler's formatter dynamically (via a
        provider), so no manual re-sync is needed here.
        """
        super().setFormatter(fmt)
```

In `console_backend.py`, change `__init__` to accept a provider and `_worker` to read it:

```python
    def __init__(
        self,
        formatter_provider: Callable[[], logging.Formatter | None],
        running_event: asyncio.Event,
        maxsize: int = 10000,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
    ) -> None:
        ...
        self.formatter_provider = formatter_provider
```

In `_worker`, replace `if self.formatter:` with `formatter = self.formatter_provider(); if formatter:` and use `formatter.format(record)`.

- [ ] **Step 4: Update the existing console tests**

`tests/test_console_backend.py` constructs `ConsoleBackend(FakeFormatter(), running_event)` directly (e.g. lines 48, 65, 85, 103, 132, 150). Update each to pass a provider: `ConsoleBackend(lambda: FakeFormatter(), running_event)`. Also update `test_basic_handler.py:121` (`assert handler._console_backend.formatter is formatter`) — this assertion must change to verify the provider returns the current formatter, e.g. `assert handler._console_backend.formatter_provider() is formatter`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_console_backend.py tests/test_basic_handler.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/console_backend.py src/scietex/logging/basic_handler.py tests/test_console_backend.py tests/test_basic_handler.py
git commit -m "refactor: console reads formatter dynamically, drop manual re-sync (AR-030)"
```

---

## Task Cluster C: Broker worker backoff (AR-022)

### Task 6: AR-022 — capped exponential backoff with jitter on connect retry

**Files:**
- Modify: `src/scietex/logging/message_broker_handler.py:139-208` (`_worker`)
- Test: `tests/test_message_broker_handler.py`

**Interfaces:**
- Consumes: existing `AsyncBrokerHandler._worker`, `self._report_error` (inherited).
- Produces: the connect-retry loop sleeps a capped, jittered exponential backoff instead of a fixed 1s. No signature change. Backoff state is local to the worker coroutine (fresh each `start_logging`).

**Background (why):** The connect-retry loop sleeps a fixed `1.0`s forever (`message_broker_handler.py:154-160`), spamming the error channel ~3600×/hr during an outage. Decision D2 sets base `0.5`s, cap `30`s, jitter ±20%, reset on successful connect.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_message_broker_handler.py`:

```python
class AlwaysFailingConnectBrokerHandler(AsyncBrokerHandler):
    """Broker whose connect() always fails, for backoff timing tests."""

    def __init__(self, *args, **kwargs):
        self.connect_attempts = 0
        self.sleeps: list[float] = []
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        raise ConnectionError("always down")

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        pass


@pytest.mark.asyncio
async def test_connect_retry_backoff_grows_and_caps(monkeypatch):
    """Connect retries sleep an increasing, capped backoff, not a fixed 1s."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)  # don't actually wait

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    handler = AlwaysFailingConnectBrokerHandler(queue_name="broker", stdout_enable=False)
    await handler.start_logging()
    # Let the worker attempt several connects.
    await _wait_for(lambda: handler.connect_attempts >= 5)
    await handler.stop_logging(timeout=0.5)

    assert len(sleeps) >= 4
    # Backoff grows from the base and is capped.
    assert sleeps[0] >= 0.4  # base 0.5 with up to 20% jitter
    assert max(sleeps) <= 30.0
    # Not a fixed 1s: later sleeps exceed the first.
    assert sleeps[-1] > sleeps[0]
```

Note: `_wait_for` already exists in `tests/test_message_broker_handler.py:25-31`. The `monkeypatch.setattr(asyncio, "sleep", ...)` must be restored before `stop_logging`'s internal waits — the worker's own `asyncio.sleep` calls are what we patch; `stop_logging` uses `wait_for`/`gather`, not bare `sleep`, so patching is safe. If flaky, assert only on the worker's recorded sleeps and keep the patch scoped.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_message_broker_handler.py::test_connect_retry_backoff_grows_and_caps -v`
Expected: FAIL — all sleeps are `1.0` (fixed), so `sleeps[-1] > sleeps[0]` is false.

- [ ] **Step 3: Implement the backoff**

In `_worker` (`message_broker_handler.py:150-160`), replace the fixed `await asyncio.sleep(1.0)` with a backoff that resets on success. Add module-level constants and a helper:

```python
_CONNECT_BACKOFF_BASE = 0.5  # seconds
_CONNECT_BACKOFF_CAP = 30.0  # seconds
_CONNECT_BACKOFF_JITTER = 0.2  # +/- 20%
```

Add a helper function near the top of the module:

```python
def _backoff_delay(attempt: int) -> float:
    """Return the capped, jittered exponential backoff delay for a connect attempt.

    Doubles from ``_CONNECT_BACKOFF_BASE`` each consecutive failure, capped at
    ``_CONNECT_BACKOFF_CAP``, with +/- ``_CONNECT_BACKOFF_JITTER`` uniform jitter
    so concurrent workers do not reconnect in lockstep.
    """
    import random

    delay = min(_CONNECT_BACKOFF_BASE * (2 ** max(attempt - 1, 0)), _CONNECT_BACKOFF_CAP)
    jitter = 1.0 + random.uniform(-_CONNECT_BACKOFF_JITTER, _CONNECT_BACKOFF_JITTER)
    return delay * jitter
```

Restructure the worker's connect path to track consecutive failures and reset on success:

```python
        try:
            connect_failures = 0
            while (
                self.logging_running_event.is_set() or not self.log_queues[self.queue_name].empty()
            ):
                if self.client is None:
                    try:
                        await self.connect()
                        connect_failures = 0
                    except Exception as exc:
                        connect_failures += 1
                        self._report_error(None, exc)
                        await asyncio.sleep(_backoff_delay(connect_failures))
                        continue
                ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_message_broker_handler.py::test_connect_retry_backoff_grows_and_caps -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. Note `test_connect_failure_surfaced_and_retried` (`tests/test_message_broker_handler.py:122-143`) and `test_stop_logging_cancels_stuck_connect_worker` (`:99-118`) must stay green — the backoff only affects the sleep between retries, not the retry/success logic.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/message_broker_handler.py tests/test_message_broker_handler.py
git commit -m "fix: capped exponential backoff with jitter on connect retry (AR-022)"
```

---

## Task Cluster D: Formatter injection (AR-024)

### Task 7: AR-024 — optional `formatter=` construction kwarg

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:111-164` (`__init__`), `src/scietex/logging/basic_handler.py:33-74` (`__init__`), `src/scietex/logging/message_broker_handler.py:41-82` (`__init__`), `src/scietex/logging/redis_handler.py:37-82` (`__init__`), `src/scietex/logging/valkey_handler.py:37-86` (`__init__`)
- Test: `tests/test_async_logging_handler.py`, `tests/test_basic_handler.py`
- Docs: `docs/configuration.md`

**Interfaces:**
- Consumes: existing `ScietexFormatter` default construction.
- Produces: every handler constructor accepts an optional keyword-only `formatter: logging.Formatter | None = None`. When `None`, the default `ScietexFormatter` is built as today; when provided, it is used. `setFormatter` still works post-hoc. No existing call site changes.

**Background (why):** `AsyncLoggingHandler.__init__` hard-codes `ScietexFormatter` (`async_logging_handler.py:162-164`); substitution is only possible post-hoc via `setFormatter`. Decision D3 adds the kwarg. Safe because AR-018 decoupled broker serialization from the formatter.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_async_logging_handler.py`:

```python
def test_formatter_kwarg_is_used():
    """A formatter passed at construction is used instead of the default ScietexFormatter."""
    custom = logging.Formatter("%(message)s")
    handler = BareHandler(service_name="TestService", worker_id=1, formatter=custom)
    assert handler.formatter is custom
```

Add `import logging` to the test file if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_formatter_kwarg_is_used -v`
Expected: FAIL — `TypeError` (no `formatter` kwarg) or the default formatter is used.

- [ ] **Step 3: Implement the kwarg**

In `AsyncLoggingHandler.__init__` (`async_logging_handler.py:111-120`), add the parameter and use it:

```python
    def __init__(
        self,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
        stdout_enable: bool = True,
        backend_config: RedisConfig | ValkeyConfig | None = None,
        formatter: logging.Formatter | None = None,
    ) -> None:
```

Replace the hard-coded construction (`async_logging_handler.py:162-164`):

```python
        self.formatter = formatter or ScietexFormatter(
            service_name=self.config.service_name, worker_id=self.config.worker_id
        )
```

Thread the kwarg through `AsyncBaseHandler.__init__` (`basic_handler.py:33-74`), `AsyncBrokerHandler.__init__` (`message_broker_handler.py:41-82`), `AsyncRedisHandler.__init__` (`redis_handler.py:37-82`), and `AsyncValkeyHandler.__init__` (`valkey_handler.py:37-86`), each adding `formatter: logging.Formatter | None = None` as a keyword-only param and forwarding it to `super().__init__(..., formatter=formatter)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_async_logging_handler.py::test_formatter_kwarg_is_used -v`
Expected: PASS.

- [ ] **Step 5: Add a broker-level test**

Append to `tests/test_message_broker_handler.py`:

```python
@pytest.mark.asyncio
async def test_broker_formatter_kwarg_does_not_change_wire_format():
    """A plain formatter passed at construction leaves broker name/time intact (AR-018)."""
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        formatter=logging.Formatter("%(message)s"),
    )
    await handler.start_logging()
    record = _make_record("hello")
    handler.emit(record)
    await _wait_for(lambda: bool(handler.sent))
    entry = handler.sent[0]
    assert entry["name"] == "TestService:1"
    assert entry["time"] == datetime.fromtimestamp(record.created, timezone.utc).isoformat()
    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Update docs**

In `docs/configuration.md`, add a note under "Custom Formatters" (`docs/configuration.md:178-195`) that a formatter can be passed at construction:

```markdown
A formatter can also be supplied at construction time via the keyword-only
`formatter=` argument on any handler, instead of calling `setFormatter()` after
construction:

```python
handler = AsyncBaseHandler(formatter=logging.Formatter("%(levelname)s | %(message)s"))
```
```

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py src/scietex/logging/basic_handler.py src/scietex/logging/message_broker_handler.py src/scietex/logging/redis_handler.py src/scietex/logging/valkey_handler.py tests/test_async_logging_handler.py tests/test_message_broker_handler.py docs/configuration.md
git commit -m "feat: accept optional formatter kwarg at construction (AR-024)"
```

---

## Task Cluster E: Backend config-input unification (AR-025)

### Task 8: AR-025 — unify backend config input to dict, translate inside `connect()`

**Files:**
- Modify: `src/scietex/logging/valkey_handler.py:37-101` (`__init__`, `connect`), `src/scietex/logging/redis_handler.py:37-100` (`__init__`, `connect`)
- Test: `tests/test_valkey_handler.py`, `tests/test_redis_handler.py`
- Docs: `docs/backends.md`, `docs/configuration.md`

**Interfaces:**
- Consumes: existing `AsyncValkeyHandler(stream_name, ..., valkey_config=...)` and `AsyncRedisHandler(stream_name, ..., redis_config=...)`.
- Produces: `AsyncValkeyHandler` accepts `valkey_config: dict | GlideClientConfiguration | None`. A dict is translated to a `GlideClientConfiguration` inside `connect()`; a `GlideClientConfiguration` is used as-is (backward compatible). `AsyncRedisHandler` unchanged in input shape (already dict). No existing call site breaks.

**Background (why):** Redis takes `redis_config: dict | None` (`redis_handler.py:43`); Valkey takes `valkey_config: GlideClientConfiguration | None` (`valkey_handler.py:43`), leaking a third-party type into the public constructor. Decision D4 unifies on dict.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_valkey_handler.py`:

```python
def test_valkey_config_accepts_dict():
    """A plain dict valkey_config is accepted and translated inside connect()."""
    handler = AsyncValkeyHandler(
        stream_name="s",
        valkey_config={"addresses": [{"host": "example.com", "port": 7000}]},
    )
    assert handler.config.backend_config.addresses == [("example.com", 7000)]
    # client_config is built lazily in connect(); until then it is None or the raw dict.
    assert handler.client_config is not None


def test_valkey_config_accepts_single_node_dict_shorthand():
    """A single-node dict shorthand {host, port} is accepted."""
    handler = AsyncValkeyHandler(
        stream_name="s", valkey_config={"host": "example.com", "port": 7000}
    )
    assert handler.config.backend_config.addresses == [("example.com", 7000)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_valkey_handler.py::test_valkey_config_accepts_dict -v`
Expected: FAIL — `TypeError` or wrong addresses (dict has no `.addresses`).

- [ ] **Step 3: Implement dict translation**

In `valkey_handler.py`, change `__init__` (`valkey_handler.py:71-88`) to accept a dict and defer `GlideClientConfiguration` construction to `connect()`:

```python
    def __init__(
        self,
        stream_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        valkey_config: dict | GlideClientConfiguration | None = None,
        error_handler: ... = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
        formatter: logging.Formatter | None = None,
    ) -> None:
        ...
        # Accept a plain dict (the package-level config story) or a vendor
        # GlideClientConfiguration (backward compatible). A dict is translated to
        # the vendor type inside connect() so the vendor type never leaks into the
        # public constructor.
        self._valkey_config_input = valkey_config
        addresses = self._extract_addresses(valkey_config)
        super().__init__(
            queue_name="valkey",
            ...
            backend_config=ValkeyConfig(addresses=addresses),
        )
        self.stream_name = stream_name
        self.client_config: GlideClientConfiguration | None = None
```

Add a module-level helper to extract addresses from either input shape:

```python
def _extract_addresses(config: dict | GlideClientConfiguration | None) -> list[tuple[str, int]]:
    """Return (host, port) pairs from a dict or a GlideClientConfiguration."""
    if config is None:
        return [("localhost", 6379)]
    if isinstance(config, dict):
        if "addresses" in config:
            return [(node["host"], node["port"]) for node in config["addresses"]]
        return [(config["host"], config["port"])]
    return [(node.host, node.port) for node in config.addresses]
```

In `connect()` (`valkey_handler.py:90-101`), build the `GlideClientConfiguration` lazily:

```python
    async def connect(self) -> None:
        if self.client is None:
            if self.client_config is None:
                self.client_config = self._build_client_config(self._valkey_config_input)
            self.client = await GlideClient.create(self.client_config)
```

Add `_build_client_config`:

```python
    def _build_client_config(self, config) -> GlideClientConfiguration:
        if isinstance(config, GlideClientConfiguration):
            return config
        if isinstance(config, dict):
            if "addresses" in config:
                nodes = [NodeAddress(host=n["host"], port=n["port"]) for n in config["addresses"]]
            else:
                nodes = [NodeAddress(host=config["host"], port=config["port"])]
            return GlideClientConfiguration(nodes)
        return GlideClientConfiguration([NodeAddress()])
```

- [ ] **Step 4: Update existing Valkey tests**

`tests/test_valkey_handler.py:95-99` (`test_valkey_config_is_typed`) asserts `handler.client_config is not None` immediately after construction. Since `client_config` is now built lazily in `connect()`, update this assertion to check the input was stored, e.g. `assert handler._valkey_config_input is not None` or construct with a `GlideClientConfiguration` and assert `handler.client_config is not None` only after `connect()`. The e2e test (`test_valkey_handler_logs_to_stream`, `:29-92`) passes a `GlideClientConfiguration` and calls `start_logging()` (which triggers `connect()`), so it stays green.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_valkey_handler.py -v`
Expected: PASS (new + updated tests; e2e skips without a server).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Update docs**

In `docs/backends.md` Valkey Configuration section (`docs/backends.md:83-90`), document that `valkey_config` accepts a dict:

```markdown
- `valkey_config`: A plain dict describing the Valkey nodes, or a
  `GlideClientConfiguration` (accepted for backward compatibility). A dict uses
  either `{"addresses": [{"host": ..., "port": ...}, ...]}` or the single-node
  shorthand `{"host": ..., "port": ...}`. The handler stores a typed
  `ValkeyConfig` (a list of `(host, port)` addresses) as
  `self.config.backend_config`; the vendor `GlideClientConfiguration` is built
  inside `connect()`.
```

Update the corresponding note in `docs/configuration.md` if it describes the Valkey input shape.

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/valkey_handler.py tests/test_valkey_handler.py docs/backends.md docs/configuration.md
git commit -m "feat: accept dict valkey_config, translate to vendor type in connect (AR-025)"
```

---

## Task Cluster F: Module locality (AR-026, AR-036)

Both findings relocate a helper to a neutral leaf. AR-026 moves `level_abbreviation`; AR-036 moves `optional_dependency_error`. Both land in `config.py` (the existing stdlib-only leaf) — but AR-026 and AR-036 must not create a cycle: `config.py` already hosts `optional_dependency_error`; adding `level_abbreviation` there is fine because `config.py` imports only stdlib and neither `formatter.py` nor any handler imports `config` in a way that would cycle (they already import from `config`).

### Task 9: AR-026 — relocate `level_abbreviation` to `config.py`

**Files:**
- Modify: `src/scietex/logging/config.py` (add `level_abbreviation`), `src/scietex/logging/formatter.py:10-28` (remove def, re-export), `src/scietex/logging/message_broker_handler.py:13` (import from config)
- Test: `tests/test_config.py`, `tests/test_formatter.py`
- Docs: `docs/architecture/components.md`, `docs/architecture/structure.md`, `docs/architecture/dependencies.md`, `docs/architecture/hotspots.md`, `docs/architecture/overview.md`, `docs/architecture/data-flow.md`

**Interfaces:**
- Consumes: existing `level_abbreviation(log_level: int) -> str`.
- Produces: `level_abbreviation` is defined in `config.py`; `formatter.py` re-exports it (`from .config import level_abbreviation`) so `from scietex.logging.formatter import level_abbreviation` keeps working; `message_broker_handler.py` imports it from `config`.

**Background (why):** `level_abbreviation` is a pure level→string mapping owned by the console-rendering module (`formatter.py:10`), but the broker imports it (`message_broker_handler.py:13`) for a concern that isn't formatting. Moving it to a neutral leaf (`config.py`) lets both formatter and broker depend on it without the broker depending on the formatter.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_level_abbreviation_lives_in_config():
    """level_abbreviation is importable from the neutral config leaf."""
    from scietex.logging.config import level_abbreviation

    assert level_abbreviation(logging.INFO) == "INF"
```

Add `import logging` to `tests/test_config.py` if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_level_abbreviation_lives_in_config -v`
Expected: FAIL — `ImportError` (not defined in config yet).

- [ ] **Step 3: Move the function**

Copy `level_abbreviation` (body from `formatter.py:10-28`) into `config.py` (it needs `import logging`, already present at `config.py:5`). In `formatter.py`, remove the definition and re-export:

```python
from .config import level_abbreviation
```

In `message_broker_handler.py:13`, change the import:

```python
from .config import level_abbreviation
```

(It already imports `RedisConfig, ValkeyConfig` from `.config` at `message_broker_handler.py:12`; merge into that import line.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_formatter.py tests/test_message_broker_handler.py -v`
Expected: PASS. `tests/test_formatter.py:6` imports `level_abbreviation` from `scietex.logging.formatter` — the re-export keeps it green.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Update docs**

Update the architecture docs that cite `level_abbreviation` at `formatter.py:10`:
- `docs/architecture/components.md:36,46` — change location to `config.py` and note the formatter re-exports it.
- `docs/architecture/structure.md:30,65` — move `level_abbreviation` from the `formatter.py` row to the `config.py` row.
- `docs/architecture/dependencies.md` — update the dependency note if it describes the broker→formatter import for `level_abbreviation`.
- `docs/architecture/hotspots.md:228`, `docs/architecture/overview.md:22`, `docs/architecture/data-flow.md:37,52` — update the cited module for `level_abbreviation`.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/config.py src/scietex/logging/formatter.py src/scietex/logging/message_broker_handler.py tests/test_config.py docs/architecture/components.md docs/architecture/structure.md docs/architecture/dependencies.md docs/architecture/hotspots.md docs/architecture/overview.md docs/architecture/data-flow.md
git commit -m "refactor: move level_abbreviation to config leaf (AR-026)"
```

### Task 10: AR-036 — relocate `optional_dependency_error` out of `config.py`

**Files:**
- Modify: `src/scietex/logging/config.py:114-119` (remove), `src/scietex/logging/redis_handler.py:3,8`, `src/scietex/logging/valkey_handler.py:3,8`
- Test: `tests/test_config.py`
- Docs: `docs/architecture/components.md`, `docs/architecture/structure.md`, `docs/architecture/hotspots.md`, `docs/architecture/dependencies.md`

**Interfaces:**
- Consumes: existing `optional_dependency_error(module_name, extra) -> str`.
- Produces: `optional_dependency_error` moves to a neutral leaf. Since AR-026 already made `config.py` host `level_abbreviation`, and the review (AR-036, lines 587-588) says "if a neutral leaf already exists for `level_abbreviation` (AR-026), this helper could share it", the recommended home is a new tiny module `_deps.py` (or keep it in `config.py` if AR-026 did not move `level_abbreviation` there).

**Decision (recommended):** Create a new stdlib-only leaf module `src/scietex/logging/_deps.py` hosting both `optional_dependency_error` and (optionally) `level_abbreviation`, so `config.py` stays purely about configuration. If the executor prefers minimal churn, keeping both helpers in `config.py` is acceptable — the review explicitly calls the current placement "a defensible placement" (line 586). This task is the LOWEST priority and can be deferred or skipped if the executor judges the churn not worth it.

**Background (why):** `optional_dependency_error` is a packaging concern, not configuration (`config.py:114-119`). It landed in `config.py` only because that was the shared stdlib-only leaf.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_optional_dependency_error_importable_from_deps():
    """optional_dependency_error is importable from its neutral home."""
    from scietex.logging._deps import optional_dependency_error

    assert "pip install scietex.logging[redis]" in optional_dependency_error("redis", "redis")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_optional_dependency_error_importable_from_deps -v`
Expected: FAIL — `ImportError` (module does not exist yet).

- [ ] **Step 3: Create `_deps.py` and update importers**

Create `src/scietex/logging/_deps.py`:

```python
"""Shared stdlib-only helpers for optional-dependency handling and level mapping."""

import logging


def level_abbreviation(log_level: int) -> str:
    """Map logging levels to 3-letter abbreviations."""
    level_map: dict[int, str] = {
        logging.DEBUG: "DBG",
        logging.INFO: "INF",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRT",
    }
    return level_map.get(log_level, f"{log_level:03d}")


def optional_dependency_error(module_name: str, extra: str) -> str:
    """Return the descriptive ImportError message for a missing optional backend dependency."""
    return (
        f"The '{module_name}' module is required to use this feature. "
        f"Please install it by running:\n\n    pip install scietex.logging[{extra}]\n"
    )
```

Update `redis_handler.py:3` and `valkey_handler.py:3` to import `optional_dependency_error` from `._deps` instead of `.config`. Update `formatter.py` and `message_broker_handler.py` to import `level_abbreviation` from `._deps` (if AR-026 moved it to config, this task re-homes it to `_deps`; keep the `config.py`/`formatter.py` re-exports working or update all importers consistently).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_formatter.py tests/test_message_broker_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Update docs**

Update `docs/architecture/structure.md:31` (config.py row) and `docs/architecture/hotspots.md:169-171` to cite `_deps.py` for `optional_dependency_error` (and `level_abbreviation` if moved there). Update `docs/architecture/components.md` and `docs/architecture/dependencies.md` similarly.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/_deps.py src/scietex/logging/config.py src/scietex/logging/formatter.py src/scietex/logging/message_broker_handler.py src/scietex/logging/redis_handler.py src/scietex/logging/valkey_handler.py tests/test_config.py docs/architecture/structure.md docs/architecture/hotspots.md docs/architecture/components.md docs/architecture/dependencies.md
git commit -m "refactor: move optional-dependency helper to _deps leaf (AR-036)"
```

---

## Task Cluster G: Redis/Valkey adapter hardening (AR-033, AR-034)

Both findings touch the concrete adapters' `connect()`/`send_message()`.

### Task 11: AR-033 — Redis `connect()` closes the local client when `ping()` fails

**Files:**
- Modify: `src/scietex/logging/redis_handler.py:97-100` (`connect`)
- Test: `tests/test_redis_handler.py`

**Interfaces:**
- Consumes: existing `AsyncRedisHandler.connect()`.
- Produces: when `ping()` raises, the locally created client is closed before re-raising. No signature change.

**Background (why):** `connect()` creates `client = await redis.Redis(...)` then `await client.ping()` (`redis_handler.py:98-99`). If `ping()` raises, `self.client` stays `None` (correct) but the local `client` and its pool are abandoned without `aclose()`. This runs every second during an outage (AR-022).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_redis_handler.py`:

```python
@pytest.mark.asyncio
async def test_connect_closes_client_when_ping_fails(monkeypatch):
    """A ping failure closes the locally created client instead of leaking it."""
    closed = []

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

        async def ping(self):
            raise ConnectionError("ping failed")

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr("scietex.logging.redis_handler.redis.Redis", FakeRedis)
    handler = AsyncRedisHandler(stream_name="s")
    with pytest.raises(ConnectionError):
        await handler.connect()
    assert handler.client is None
    assert closed == [True]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_redis_handler.py::test_connect_closes_client_when_ping_fails -v`
Expected: FAIL — `closed == []` (client leaked).

- [ ] **Step 3: Implement the fix**

In `connect()` (`redis_handler.py:97-100`):

```python
        if self.client is None:
            client = await redis.Redis(**self.client_config, decode_responses=True)
            try:
                await client.ping()
            except Exception:
                # ping() failed: close the local client so its pool is not leaked.
                # This runs on every connect attempt during an outage (AR-022).
                await client.aclose()
                raise
            self.client = client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_redis_handler.py::test_connect_closes_client_when_ping_fails -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/redis_handler.py tests/test_redis_handler.py
git commit -m "fix: close Redis client when ping fails in connect (AR-033)"
```

### Task 12: AR-034 — `send_message` raises when `self.client is None`

**Files:**
- Modify: `src/scietex/logging/redis_handler.py:120-121`, `src/scietex/logging/valkey_handler.py:122-123`
- Test: `tests/test_redis_handler.py`, `tests/test_valkey_handler.py`

**Interfaces:**
- Consumes: existing `send_message(record: dict[str, str])`.
- Produces: `send_message` raises `RuntimeError` (or `ConnectionError`) when `self.client is None`, honoring the documented "failure must raise" contract (`message_broker_handler.py:126-128`). No signature change.

**Background (why):** `send_message` silently no-ops when `self.client is None` (`redis_handler.py:120-121`, `valkey_handler.py:122-123`), so the worker calls `task_done()` and the record is silently acknowledged as delivered when nothing was sent. Currently defensive dead code (the worker connects before sending), but a latent trap under any future ordering change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_redis_handler.py`:

```python
@pytest.mark.asyncio
async def test_send_message_raises_when_no_client():
    """send_message must raise when there is no client, not silently no-op."""
    handler = AsyncRedisHandler(stream_name="s")
    assert handler.client is None
    with pytest.raises(RuntimeError):
        await handler.send_message({"level": "INF", "message": "x", "name": "n", "time": "t"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_redis_handler.py::test_send_message_raises_when_no_client -v`
Expected: FAIL — no exception raised (silent no-op).

- [ ] **Step 3: Implement the precondition**

In `redis_handler.py:120-121`:

```python
    async def send_message(self, record: dict[str, str]) -> None:
        if self.client is None:
            raise RuntimeError("AsyncRedisHandler.send_message() called with no client")
        await self.client.xadd(self.stream_name, record)
```

In `valkey_handler.py:122-123`:

```python
    async def send_message(self, record: dict[str, str]) -> None:
        if self.client is None:
            raise RuntimeError("AsyncValkeyHandler.send_message() called with no client")
        await self.client.xadd(self.stream_name, record.items())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_redis_handler.py::test_send_message_raises_when_no_client -v`
Expected: PASS.

- [ ] **Step 5: Add the Valkey equivalent test**

Append to `tests/test_valkey_handler.py`:

```python
@pytest.mark.asyncio
async def test_send_message_raises_when_no_client():
    """send_message must raise when there is no client, not silently no-op."""
    handler = AsyncValkeyHandler(stream_name="s")
    assert handler.client is None
    with pytest.raises(RuntimeError):
        await handler.send_message({"level": "INF", "message": "x", "name": "n", "time": "t"})
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. The broker worker always connects before sending, so no existing path hits the new raise.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/redis_handler.py src/scietex/logging/valkey_handler.py tests/test_redis_handler.py tests/test_valkey_handler.py
git commit -m "fix: send_message raises when no client, honoring must-raise contract (AR-034)"
```

---

## Task Cluster H: Testability & public surface (AR-029, AR-035, AR-027)

### Task 13: AR-029 — Redis e2e test skip guard

**Files:**
- Modify: `tests/test_redis_handler.py:14-69`
- Test: `tests/test_redis_handler.py`

**Interfaces:**
- Consumes: existing `test_redis_handler_logs_to_stream`.
- Produces: the Redis e2e test skips when no Redis server is reachable, mirroring the Valkey guard (`tests/test_valkey_handler.py:15-27`).

**Background (why):** `test_redis_handler_logs_to_stream` connects to `localhost:6379` with no skip guard (`tests/test_redis_handler.py:14-69`), so `uv run pytest` fails on any machine/CI without a Redis server, whereas the Valkey test cleanly skips.

- [ ] **Step 1: Add the skip guard**

Add a reachability helper and `skipif` decorator to `tests/test_redis_handler.py`, mirroring `tests/test_valkey_handler.py:15-27`:

```python
import socket


def _redis_server_reachable() -> bool:
    """Return True when a TCP connection to the local Redis node can be established."""
    try:
        with socket.create_connection(("localhost", 6379), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _redis_server_reachable(),
    reason="No Redis server reachable at localhost:6379; skipping end-to-end test.",
)
@pytest.mark.asyncio
async def test_redis_handler_logs_to_stream(): ...
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_redis_handler.py::test_redis_handler_logs_to_stream -v`
Expected: PASS if a Redis server is running, or SKIP if not. Either way the suite no longer hard-fails without Redis.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (or skip the Redis e2e when no server).

- [ ] **Step 4: Commit**

```bash
git add tests/test_redis_handler.py
git commit -m "test: skip Redis e2e test when no server reachable (AR-029)"
```

### Task 14: AR-027 — tighten the `__init__.py` optional-import guard

**Files:**
- Modify: `src/scietex/logging/__init__.py:116-127`
- Test: `tests/test_version.py` (or a new small test)

**Interfaces:**
- Consumes: existing guarded imports of `redis_handler`/`valkey_handler`.
- Produces: an `ImportError` whose `name` is not the expected optional dependency is re-raised (not swallowed); a missing optional dependency still degrades gracefully. No public signature change.

**Background (why):** The `try/except ImportError: pass` guard (`__init__.py:116-127`) swallows any `ImportError` raised while importing a backend module — not just the missing-optional-client case — including a bug or a missing transitive dependency inside `redis_handler.py`. The descriptive message (`config.py:114-119`) is only reachable via a direct `import scietex.logging.redis_handler`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_version.py` (or a new `tests/test_package_imports.py`):

```python
def test_optional_import_reraises_non_dependency_errors(monkeypatch):
    """An ImportError not caused by a missing optional dep is re-raised, not swallowed."""
    import importlib
    import scietex.logging as pkg

    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "scietex.logging.redis_handler":
            raise ImportError("boom: broken transitive import", name="some_other_module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ImportError):
        importlib.reload(pkg)
```

Note: reloading the package is fragile. A more robust test imports the module fresh in a subprocess or checks the guard logic directly. If reload proves flaky, test the guard by asserting that importing `scietex.logging` with a monkeypatched `redis_handler` that raises a non-dependency `ImportError` propagates. The executor should prefer the least fragile approach; if reload is unreliable, assert the behavior via a direct unit test of a small extracted helper.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py -v`
Expected: FAIL — the non-dependency ImportError is swallowed (package imports cleanly).

- [ ] **Step 3: Implement the tightened guard**

Replace the two `try/except ImportError: pass` blocks (`__init__.py:116-127`) with a guard that re-raises non-dependency errors. The cleanest approach: import the backend module and check the exception's `.name`:

```python
def _import_optional(module_name: str, dep_name: str) -> Any:
    """Import an optional backend module, degrading only on a missing optional dep.

    Any ImportError whose ``name`` is not the expected optional dependency is
    re-raised so genuine import bugs (broken transitive imports, code errors)
    surface instead of being silently hidden by the optional-import guard.
    """
    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        if exc.name == dep_name or (exc.name or "").startswith(dep_name):
            return None
        raise


_redis_mod = _import_optional("scietex.logging.redis_handler", "redis")
if _redis_mod is not None:
    AsyncRedisHandler = _redis_mod.AsyncRedisHandler
    __all__ += ["AsyncRedisHandler"]

_valkey_mod = _import_optional("scietex.logging.valkey_handler", "valkey_glide")
if _valkey_mod is not None:
    AsyncValkeyHandler = _valkey_mod.AsyncValkeyHandler
    __all__ += ["AsyncValkeyHandler"]
```

Note: the `redis_handler` module raises `ImportError(optional_dependency_error(...)) from e` where the chained cause `e` has `.name == "redis"` (`redis_handler.py:5-8`). The guard must inspect the *chained* cause's `.name`, not the outer message. Adjust the check to walk `exc.__cause__`:

```python
    except ImportError as exc:
        cause = exc.__cause__
        cause_name = getattr(cause, "name", None)
        if cause_name == dep_name:
            return None
        raise
```

The executor must verify the actual `.name` value the installed `redis`/`glide` import raises and match `dep_name` accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_version.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. The package must still import cleanly with and without the optional deps installed.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/__init__.py tests/test_version.py
git commit -m "fix: re-raise non-dependency ImportErrors in optional backend guard (AR-027)"
```

### Task 15: AR-035 — export config types, align AGENTS.md

**Files:**
- Modify: `src/scietex/logging/__init__.py:108-114` (`__all__`), `AGENTS.md:50-58` (Public API section)
- Test: `tests/test_version.py` (or a new small test)
- Docs: `docs/architecture/components.md:15-18`, `docs/architecture/structure.md:26`

**Interfaces:**
- Consumes: existing `LoggingConfig`, `RedisConfig`, `ValkeyConfig` from `config.py`.
- Produces: `__all__` includes `LoggingConfig`, `RedisConfig`, `ValkeyConfig` (per Decision D5); `AGENTS.md` Public API lists `AsyncLoggingHandler`, `ConsoleBackend`, and the config types.

**Background (why):** `__all__` exports `ConsoleBackend` (`__init__.py:112`) but not the config types; `AGENTS.md`'s Public API omits `AsyncLoggingHandler` and `ConsoleBackend`. Decision D5 exports the config types and aligns the docs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_version.py`:

```python
def test_public_api_exports_config_types():
    """LoggingConfig, RedisConfig, ValkeyConfig are importable from the package root."""
    from scietex.logging import LoggingConfig, RedisConfig, ValkeyConfig

    assert LoggingConfig is not None
    assert RedisConfig is not None
    assert ValkeyConfig is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_version.py::test_public_api_exports_config_types -v`
Expected: FAIL — `ImportError` (config types not exported).

- [ ] **Step 3: Export the config types**

In `__init__.py`, add the config imports and extend `__all__`:

```python
from .config import LoggingConfig, RedisConfig, ValkeyConfig

__all__ = [
    "AsyncBaseHandler",
    "AsyncBrokerHandler",
    "AsyncLoggingHandler",
    "ConsoleBackend",
    "LoggingConfig",
    "RedisConfig",
    "ScietexFormatter",
    "ValkeyConfig",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_version.py::test_public_api_exports_config_types -v`
Expected: PASS.

- [ ] **Step 5: Align AGENTS.md**

Update the "Exported Classes (from `__init__.py`)" list in `AGENTS.md:50-58` to include `AsyncLoggingHandler`, `ConsoleBackend`, and the config types:

```markdown
### Exported Classes (from `__init__.py`)

- `AsyncLoggingHandler` - Pure machinery base (queue/worker/event control; no sink of its own)
- `AsyncBaseHandler` - Base handler with console logging backend (always available)
- `AsyncBrokerHandler` - Base handler for message broker backends
- `ConsoleBackend` - Console (stdout) sink registered as a peer backend by `AsyncBaseHandler`
- `ScietexFormatter` - Custom formatter with worker name and 3-letter log level abbreviations
- `LoggingConfig` / `RedisConfig` / `ValkeyConfig` - Typed configuration objects (single source of truth)
- `AsyncRedisHandler` - Redis logging backend (optional, requires `[redis]` extra)
- `AsyncValkeyHandler` - Valkey logging backend (optional, requires `[valkey]` extra)
```

- [ ] **Step 6: Update architecture docs**

Update `docs/architecture/components.md:15-18` and `docs/architecture/structure.md:26` to reflect the expanded `__all__`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/__init__.py tests/test_version.py AGENTS.md docs/architecture/components.md docs/architecture/structure.md
git commit -m "feat: export config types and align public-surface docs (AR-035)"
```

---

## Final Verification

After all tasks are complete, run the full verification set:

```bash
uv run pytest -q
uv run ruff check .
uv run ty check src/scietex/logging/
```

Expected: 80+ tests pass (Redis/Valkey e2e tests skip without servers), ruff clean, ty clean.

Then append Resolution notes to `docs/reviews/architecture/2026-09-05.md` for each resolved finding (AR-020, AR-021, AR-022, AR-024, AR-025, AR-026, AR-027, AR-028, AR-029, AR-030, AR-031, AR-033, AR-034, AR-035, AR-036), following the existing "**Resolution (AR-XXX):**" format used for the P1/P2 findings.

---

## Self-Review

**Spec coverage:** All 15 findings map to a task: AR-020→T1, AR-028→T2, AR-031→T3, AR-021→T4, AR-030→T5, AR-022→T6, AR-024→T7, AR-025→T8, AR-026→T9, AR-036→T10, AR-033→T11, AR-034→T12, AR-029→T13, AR-027→T14, AR-035→T15. Each task is independently verifiable (own test + commit). The five decision findings (AR-020, AR-022, AR-024, AR-025, AR-035) are flagged in the Decisions section with recommended defaults.

**Placeholder scan:** No TBD/TODO. Every code step shows the concrete change. The only judgment calls left to the executor are flagged explicitly (AR-027's `.name` matching must be verified against the installed client; AR-030's provider approach; AR-036's `_deps.py` vs keep-in-config).

**Type consistency:** `formatter` kwarg is threaded identically through all five handler constructors (T7). `error_handler` on `ConsoleBackend` matches the broker's `_report_error` signature `(record, exc)` (T4). `level_abbreviation`/`optional_dependency_error` keep their exact signatures across the relocation (T9/T10). `send_message` raises `RuntimeError` in both adapters (T12).

---

## Handoff Plan

1. Confirm the five decisions (D1-D5) with the user before starting Tasks 1, 6, 7, 8, 15 respectively.
2. Execute tasks in order T1→T15, each independently verifiable with its own test + commit.
3. After each task, run `uv run pytest -q` (full suite) to confirm no regression.
4. After all tasks, run the final verification set and append Resolution notes to the review doc.
- Risk: AR-027's `.name` matching must be verified against the actual installed `redis`/`glide` import error; AR-030 changes `ConsoleBackend.__init__`'s first arg (update all direct constructions in tests); AR-025 changes `client_config` to lazy (update `test_valkey_config_is_typed`).
- Test: `uv run pytest -q && uv run ruff check . && uv run ty check src/scietex/logging/` must all pass green.
