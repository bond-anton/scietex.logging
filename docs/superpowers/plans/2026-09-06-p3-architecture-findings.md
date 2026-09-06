# P3 Architecture Findings (AR-107..AR-116) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the remaining Priority-3 architecture-review findings — AR-107, AR-108, AR-109, AR-110, AR-111, AR-112, AR-115, AR-116 (AR-113 already done in P2; AR-114 explicitly accepted) — as small, independently verifiable hardening changes that keep the full suite green.

**Architecture:** No redesign. Four findings are code changes (AR-107 identity centralization, AR-108 shared error reporter, AR-110 drain default, AR-115 public worker accessor); four are documentation of existing constraints (AR-109 reserved names, AR-111 formatter non-mutation, AR-106 console-by-default, AR-112 sync error_handler); AR-116 is assessed and recommended for deferral. Findings that touch the same file are grouped into task clusters so each task is independently testable and reviewable.

**Tech Stack:** Python >=3.10, asyncio, stdlib `dataclasses`/`typing`, pytest-asyncio, ruff, ty. No new dependencies.

**Spec:** `docs/reviews/architecture/2026-09-06.md` findings AR-106 (lines 253-265), AR-107 (267-277), AR-108 (279-291), AR-109 (293-305), AR-110 (307-320), AR-111 (322-333), AR-112 (335-346), AR-115 (374-384), AR-116 (386-400), plus the P3 recommendation (lines 460-468) and target architecture (472-512). Executors read both this plan and the review.

## Global Constraints

- Working tree must stay green after each task: `uv run pytest -q`, `uv run ruff check .`, `uv run ty check src/scietex/logging/`.
- Do NOT modify files outside the exact paths listed per task. P1/P2 findings (AR-101..AR-105, AR-113) are **already resolved** — do not re-touch their resolved code except where a P3 task legitimately extends it.
- Preserve the strict one-way acyclic import graph. `config.py` and `formatter.py` import only stdlib. No module may import a handler module from `config.py` (cycle). `config.py` must never import `formatter.py` and vice-versa.
- **Public constructor API stays backward-compatible.** Every existing call site in `examples/`, `docs/`, `tests/`, and `__init__.py` docstrings must keep working unchanged. AR-116 is the only finding that would change the public constructor surface, and it is **deferred** (see Decision D1).
- No emoji in code/comments. Comments explain WHY, not WHAT. No commented-out code. No `@ts-ignore`-style suppressions.
- Every public function/method needs at least one caller before commit; no dead code.
- Commit style follows repo history: `fix: ...`, `refactor: ...`, `test: ...`, `docs: ...` conventional prefixes (see `git log --oneline`).
- The working tree is CLEAN at commit 6ffb442. Stage only the files each task lists; never `git add -A` or `git add <directory>`.

---

## Decisions Requiring Sign-off (read before Task 1)

### D1 — AR-116: constructor-surface refactor (RECOMMENDED: DEFER)

`AsyncLoggingHandler.__init__` accepts `stdout_enable`, `backend_config`, `formatter` and stores them on `config` without acting on them (`async_logging_handler.py:116-188`); five handlers repeat the full parameter list. The review's own recommendation is "consider" — it is a design smell, not a defect, and the review rates it LOW impact / Medium effort (1-2d).

**Recommended: DEFER.** Rationale:
1. It is a **public-API breaking change** touching all five constructors (`AsyncLoggingHandler`, `AsyncBaseHandler`, `AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`) plus every example, docstring, and test that constructs them. The repo's own constraint is "public constructor API stays backward-compatible unless a finding explicitly requires a decision" — AR-116 does not require one.
2. The current design is internally coherent: `LoggingConfig` is already the single runtime source of truth, and the flat aliases (`queue_maxsize`, `stdout_enable`, `error_handler`) are read-only `@property` views over it. The "funnel" is a convenience that keeps the five constructors' signatures uniform and lets `AsyncBaseHandler`/`AsyncBrokerHandler` forward kwargs unchanged.
3. A `LoggingConfig`-accepting constructor would force every caller to build a config object, breaking the ergonomic `AsyncBaseHandler(service_name=..., worker_id=...)` form that docs and examples rely on. Narrower per-layer constructors would *increase* the surface (each layer re-declares only its own params) without removing the duplication the review flags.
4. The duplication cost is real but low and already mitigated: adding an option means touching five signatures, but that is a rare event and the signatures are mechanical.

**Decision needed:** confirm deferral before Task 1. If the executor is instructed to implement it anyway, it becomes a separate, larger plan (public-API change) and must NOT be folded into this one.

### D2 — AR-110: drain-less backend policy (RECOMMENDED: default to a generic `queue.join()` drain)

`register_backend(name, queue, worker, drain=None)` only appends `drain` to `_drain_hooks` when not `None` (`async_logging_handler.py:237-238`). A backend registered without a drain hook is silently dropped at stop: `stop_logging` drains only `_drain_hooks` (`:378-380`) then clears leftover records (`:418-421`), so the backend's queued records are lost with no flush attempt and no status result.

**Recommended: default `drain` to a generic `queue.join()` drain** rather than requiring it. Rationale:
1. `register_backend` is a **public-ish extension seam** used by custom backends (the review's own `configuration.md:95` example calls `self.register_backend("my_backend", asyncio.Queue(), self._worker, self.drain)` — but a naive custom backend may omit `drain`). Requiring it would break existing custom-backend callers that omit it (e.g. `tests/test_async_logging_handler.py:208` registers with no drain and expects success).
2. A generic `queue.join()` drain is exactly what every built-in backend's `drain` does (`console_backend.py:175`, `message_broker_handler.py:245`): wait for the queue to join under the timeout, return a `BackendDrainResult`. Defaulting to it gives a drain-less backend the same graceful-flush-and-status behavior as the built-ins, with zero new code in the backend author.
3. The default must be **per-queue** (the queue passed to `register_backend`), not a shared helper, so each backend drains its own queue. It must return a `BackendDrainResult` named after the backend's `name` so status reporting stays correct.

**Decision needed:** confirm default-to-generic-drain before Task 3. If require-drain is chosen instead, Task 3 becomes a breaking change to `register_backend` and must update `tests/test_async_logging_handler.py:208` and the `configuration.md:95` example.

### D3 — AR-107: identity centralization (RECOMMENDED: expose `worker_name` as a handler property derived from config; default formatter and broker both consume it)

Identity `service_name:worker_id` is computed in two places: `formatter.py:54` (`self.worker_name = f"{service_name}:{worker_id}"`) and `message_broker_handler.py:193` (`name = f"{self.config.service_name}:{self.config.worker_id}"`). They diverge when a user injects `ScietexFormatter(service_name="B")` into a handler built with `service_name="A"` (console prints "B", broker sends "A").

**Recommended:** add a read-only `worker_name` property on `AsyncLoggingHandler` derived from `self.config` (`f"{self.config.service_name}:{self.config.worker_id}"`), and have the broker worker consume `self.worker_name` instead of recomputing the f-string. The **default** `ScietexFormatter` is already constructed from `self.config.service_name`/`worker_id` (`async_logging_handler.py:174-176`), so it already agrees with the handler property. A **user-injected** formatter keeps its own `worker_name` (it is a standalone `logging.Formatter` the user built with whatever identity they chose) — that is the documented, correct behavior, not a divergence to "fix".

Rationale:
- The handler property makes `config` the single owner of identity for everything the handler itself derives (broker payload, default formatter). The formatter's `worker_name` is *its own* attribute, correct for a standalone formatter; the handler property is the handler's identity.
- This is non-breaking: `worker_name` is a new read-only property; no existing attribute named `worker_name` exists on the handler. The broker's wire output is byte-identical (`self.worker_name` == the old f-string).
- It does NOT change `ScietexFormatter.__init__`'s signature or behavior, so `test_formatter.py` and `test_async_logging_handler.py:75,112` stay green.

**Decision needed:** confirm the property approach (vs. deriving the formatter's worker_name from config, which would break the standalone-formatter contract and the `formatter=` injection test at `test_async_logging_handler.py:78-83`). The property approach is chosen because it centralizes identity for the handler's own consumers without changing the user-injectable formatter's semantics.

---

## File Structure

Files modified by this plan, and the findings each carries:

| File | Findings | Change |
|------|----------|--------|
| `src/scietex/logging/async_logging_handler.py` | AR-107, AR-108, AR-110 | Add `worker_name` property; delegate `_report_error` to shared helper; default drain in `register_backend` |
| `src/scietex/logging/console_backend.py` | AR-108, AR-115 | Delegate `_report_error` to shared helper; add public `worker` property |
| `src/scietex/logging/basic_handler.py` | AR-115 | Use `backend.worker` instead of `backend._worker` |
| `src/scietex/logging/message_broker_handler.py` | AR-107 | Broker worker consumes `self.worker_name` |
| `src/scietex/logging/config.py` | AR-108 | Host the shared `report_error` helper (neutral stdlib-only leaf) |
| `docs/configuration.md` | AR-109, AR-111, AR-112 | Reserved names; formatter non-mutation; sync error_handler |
| `docs/advanced.md` | AR-109, AR-106 | Reserved names for custom backends; console-by-default note |
| `docs/architecture/components.md` | AR-107, AR-108, AR-110, AR-115 | Reflect new public surface |
| `tests/test_async_logging_handler.py` | AR-107, AR-110 | New tests |
| `tests/test_console_backend.py` | AR-108, AR-115 | New tests |
| `tests/test_basic_handler.py` | AR-115 | Assert public worker seam |
| `tests/test_message_broker_handler.py` | AR-107 | Assert broker uses handler identity |

**Import-direction note (AR-108 home):** `console_backend.py` already imports `BackendDrainResult, DrainStatus` from `async_logging_handler.py` (`console_backend.py:15`). So `async_logging_handler` is NOT a neutral leaf for the shared helper — putting `report_error` there would keep `console_backend` importing from it (fine, no cycle) but would NOT let `config.py`-style neutrality be achieved, and `async_logging_handler` is not a leaf. The genuinely neutral stdlib-only leaf is `config.py` (imports only stdlib; `formatter.py` and all handlers already import from it). **The shared helper goes in `config.py`.** This is consistent with the existing pattern: `config.py` already hosts `validate_queue_maxsize`, `level_abbreviation`, and `optional_dependency_error` as cross-module stdlib-only helpers (AR-114 accepted this as the designated dumping ground). Adding `report_error` there is the same decision, not a new one.

---

## Task 1: AR-107 — Centralize handler identity as a `worker_name` property

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py` (add property near the `error_handler`/`queue_maxsize` aliases, ~line 190-200)
- Modify: `src/scietex/logging/message_broker_handler.py:193`
- Test: `tests/test_async_logging_handler.py`, `tests/test_message_broker_handler.py`
- Docs: `docs/architecture/components.md`

**Interfaces:**
- Consumes: `self.config.service_name`, `self.config.worker_id` (already present).
- Produces: `AsyncLoggingHandler.worker_name` — read-only `str` property `f"{self.config.service_name}:{self.config.worker_id}"`. `AsyncBrokerHandler._worker` reads `self.worker_name`.

- [ ] **Step 1: Write the failing test** (in `tests/test_async_logging_handler.py`, after `test_config_is_single_source_of_truth`)

```python
def test_worker_name_property_derives_from_config():
    """worker_name is a read-only property derived from config (AR-107)."""
    handler = BareHandler(service_name="Svc", worker_id=7)
    assert handler.worker_name == "Svc:7"
    assert handler.worker_name == f"{handler.config.service_name}:{handler.config.worker_id}"
    with pytest.raises(AttributeError):
        handler.worker_name = "other"  # read-only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_worker_name_property_derives_from_config -v`
Expected: FAIL with `AttributeError: 'BareHandler' object has no attribute 'worker_name'`

- [ ] **Step 3: Add the property** in `async_logging_handler.py`, after the `queue_maxsize` property (line ~200):

```python
    @property
    def worker_name(self) -> str:
        """Read-only handler identity ``service_name:worker_id`` derived from config.

        The single owner of handler identity is ``config``; the default
        ``ScietexFormatter`` is built from the same fields and the broker worker
        reads this property, so console and broker output cannot diverge (AR-107).
        A user-injected formatter keeps its own ``worker_name``.
        """
        return f"{self.config.service_name}:{self.config.worker_id}"
```

- [ ] **Step 4: Update the broker worker** in `message_broker_handler.py:193` to consume the property:

```python
                name = self.worker_name
```

(Replace `name = f"{self.config.service_name}:{self.config.worker_id}"`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_message_broker_handler.py -q`
Expected: PASS (all existing broker identity tests at `test_message_broker_handler.py:311,335` still assert `"TestService:1"`, which `self.worker_name` produces identically).

- [ ] **Step 6: Update `docs/architecture/components.md`** — in the `AsyncLoggingHandler` public-interface list (near line 70-86), add a line documenting the new property, and update the broker log-entry dict shape note (lines 242-249) to say `name` derives from `self.worker_name` (the handler identity property), not a recomputed f-string.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py src/scietex/logging/message_broker_handler.py tests/test_async_logging_handler.py docs/architecture/components.md
git commit -m "refactor: centralize handler identity as worker_name property (AR-107)"
```

---

## Task 2: AR-108 — Extract a shared `report_error` helper into `config.py`

**Files:**
- Modify: `src/scietex/logging/config.py` (add `report_error` helper)
- Modify: `src/scietex/logging/async_logging_handler.py` (delete `_error_logger` + `_report_error` body, delegate)
- Modify: `src/scietex/logging/console_backend.py` (delete `_error_logger` + `_report_error` body, delegate)
- Test: `tests/test_async_logging_handler.py`, `tests/test_console_backend.py`
- Docs: `docs/architecture/components.md`

**Interfaces:**
- Consumes: `logging`, `Callable` (already imported in `config.py`).
- Produces: `config.report_error(handler_name: str, error_handler: Callable[[logging.LogRecord | None, Exception], None] | None, record: logging.LogRecord | None, exc: Exception) -> None` — the shared error-routing policy: invoke `error_handler(record, exc)` if set; on a raising handler, fall through to the `scietex.logging` module logger. The module logger is defined once in `config.py`.

**Design decision — where the helper lives:** `config.py` is the neutral stdlib-only leaf (imports only stdlib). `console_backend.py` already imports from `async_logging_handler.py` (`console_backend.py:15`), so `async_logging_handler` is not a leaf and cannot host a helper that `config.py`-style neutrality requires. `config.py` already hosts the other cross-module stdlib-only helpers (`validate_queue_maxsize`, `level_abbreviation`, `optional_dependency_error`) — AR-114 explicitly accepted this as the designated helper leaf. Adding `report_error` there is the same accepted decision, not a new dumping-ground expansion.

**Behavioral note:** the two current `_report_error` bodies differ only in the log message prefix: `async_logging_handler.py:488-492` logs `"%s failed to deliver a log record: %s"` with `type(self).__name__`; `console_backend.py:154-157` logs `"ConsoleBackend failed to deliver a log record: %s"`. The shared helper takes `handler_name` and formats `"%s failed to deliver a log record: %s"`. The console's message becomes `"ConsoleBackend failed to deliver a log record: ..."` — **identical** to today (the console passes `"ConsoleBackend"`). The handler's message becomes `"AsyncLoggingHandler failed to deliver..."` etc. — identical to today (`type(self).__name__`). No test asserts the exact class-name prefix beyond the substring `"failed to deliver a log record"` (`test_async_logging_handler.py:235`, `test_console_backend.py:210`), so both stay green.

- [ ] **Step 1: Write the failing test** (in `tests/test_console_backend.py`, after `test_worker_reports_via_module_logger_when_no_error_handler`)

```python
def test_report_error_helper_routes_to_handler_or_module_logger(caplog):
    """The shared report_error helper honors the error_handler and falls back (AR-108)."""
    from scietex.logging.config import report_error

    calls = []
    report_error("ConsoleBackend", lambda r, e: calls.append(e), None, RuntimeError("boom"))
    assert len(calls) == 1

    def bad(record, exc):
        raise RuntimeError("handler broken")

    with caplog.at_level(logging.ERROR, logger="scietex.logging"):
        report_error("ConsoleBackend", bad, None, RuntimeError("boom"))
    assert any("failed to deliver a log record" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_console_backend.py::test_report_error_helper_routes_to_handler_or_module_logger -v`
Expected: FAIL with `ImportError: cannot import name 'report_error' from 'scietex.logging.config'`

- [ ] **Step 3: Add the helper to `config.py`** (after `optional_dependency_error`, ~line 158):

```python
def report_error(
    handler_name: str,
    error_handler: Callable[[logging.LogRecord | None, Exception], None] | None,
    record: logging.LogRecord | None,
    exc: Exception,
) -> None:
    """Report a delivery error through the configured error channel (AR-108).

    Shared by the handler machinery and the console backend so the
    "fall through to the module logger on a raising error_handler" guarantee is
    single-sourced. If ``error_handler`` is set it is invoked with ``(record,
    exc)``; otherwise (or if it raises) the error is logged through the
    ``scietex.logging`` module logger. ``handler_name`` names the reporting
    component in the log line.

    Args:
        handler_name (str): The reporting component's name (e.g. the class name).
        error_handler (Callable | None): Optional ``(record, exc)`` callback.
        record (logging.LogRecord | None): The record whose delivery failed, or
            None when the failure is not tied to a specific record.
        exc (Exception): The exception that caused the failure.
    """
    if error_handler is not None:
        try:
            error_handler(record, exc)
            return
        except Exception:
            # The error reporter must never crash the logging path, but a
            # raising user callback must not swallow the telemetry either.
            # Fall through to the module logger below.
            pass
    _error_logger.error(
        "%s failed to deliver a log record: %s",
        handler_name,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
```

Add the module logger at the top of `config.py` (after the imports, ~line 14):

```python
_error_logger = logging.getLogger("scietex.logging")
```

- [ ] **Step 4: Delegate in `async_logging_handler.py`**

Delete the module-level `_error_logger = logging.getLogger("scietex.logging")` (line 21). Replace the body of `_report_error` (lines 465-493) with a delegation, keeping the method (it is called by `emit`/`handleError` and passed as `error_handler=self._report_error` to `ConsoleBackend` in `basic_handler.py:86`):

```python
    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a delivery error through the configured error channel.

        Delegates to the shared ``config.report_error`` helper (AR-108) so the
        handler and the console backend share one error-routing policy.
        """
        report_error(type(self).__name__, self.config.error_handler, record, exc)
```

Add the import at the top of `async_logging_handler.py` (line 18):

```python
from .config import LoggingConfig, RedisConfig, ValkeyConfig, report_error, validate_queue_maxsize
```

- [ ] **Step 5: Delegate in `console_backend.py`**

Delete the module-level `_error_logger = logging.getLogger("scietex.logging")` (line 17). Replace the body of `_report_error` (lines 131-158) with a delegation:

```python
    def _report_error(self, record: logging.LogRecord | None, exc: Exception) -> None:
        """Report a console delivery error through the configured error channel.

        Delegates to the shared ``config.report_error`` helper (AR-108).
        """
        report_error("ConsoleBackend", self.error_handler, record, exc)
```

Add the import at the top of `console_backend.py` (line 15):

```python
from .async_logging_handler import BackendDrainResult, DrainStatus
from .config import report_error
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_console_backend.py tests/test_basic_handler.py -q`
Expected: PASS. The existing fallback tests (`test_async_logging_handler.py:215-237`, `test_console_backend.py:200-216`) assert the substring `"failed to deliver a log record"` and the module logger name `"scietex.logging"`, both preserved.

- [ ] **Step 7: Update `docs/architecture/components.md`** — in the `config.py` section, add `report_error` to the listed helpers; note the module logger now lives in `config.py`.

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/config.py src/scietex/logging/async_logging_handler.py src/scietex/logging/console_backend.py tests/test_console_backend.py docs/architecture/components.md
git commit -m "refactor: extract shared report_error helper into config leaf (AR-108)"
```

---

## Task 3: AR-110 — Default `register_backend` to a generic `queue.join()` drain

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py` (`register_backend`, lines 202-238)
- Test: `tests/test_async_logging_handler.py`
- Docs: `docs/architecture/components.md`

**Interfaces:**
- Consumes: `BackendDrainResult`, `DrainStatus` (defined in this module), the `queue` and `name` args of `register_backend`.
- Produces: `register_backend(name, queue, worker, drain=None)` — when `drain` is `None`, a default drain hook is registered that awaits `queue.join()` under the timeout and returns a `BackendDrainResult(name, ...)`. Signature unchanged; behavior for drain-less backends changes from "silently dropped at stop" to "gracefully drained and status-reported".

**Design decision — default, not require:** `register_backend` is a public extension seam; `tests/test_async_logging_handler.py:208` and the `configuration.md:95` example register backends without a drain and expect success. Requiring a drain would break those. A generic `queue.join()` drain matches exactly what every built-in backend's `drain` does (`console_backend.py:175`, `message_broker_handler.py:245`), so defaulting gives drain-less custom backends the same graceful-flush-and-status behavior with no new backend code.

- [ ] **Step 1: Write the failing test** (in `tests/test_async_logging_handler.py`, after `test_register_backend_duplicate_name_raises`)

```python
@pytest.mark.asyncio
async def test_register_backend_without_drain_gets_default_queue_join_drain():
    """A backend registered without a drain hook gets a generic queue.join() drain (AR-110)."""
    handler = BareHandler()
    queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue()
    delivered: list[str] = []

    async def worker() -> None:
        while handler.logging_running_event.is_set() or not queue.empty():
            try:
                record = await asyncio.wait_for(queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            delivered.append(record.getMessage())
            queue.task_done()

    handler.register_backend("nodrain", queue, worker)  # no drain arg

    assert len(handler._drain_hooks) == 1  # a default drain hook was registered

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: len(delivered) == 1)
    await handler.stop_logging(timeout=0.5)

    assert delivered == ["hello"]
    assert queue.empty()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_register_backend_without_drain_gets_default_queue_join_drain -v`
Expected: FAIL — `handler._drain_hooks == []` (assertion at `len(handler._drain_hooks) == 1` fails), and/or the record is dropped at stop.

- [ ] **Step 3: Implement the default drain** in `register_backend` (`async_logging_handler.py:202-238`). Replace the `if drain is not None:` guard with a default:

```python
        if drain is None:
            # A backend registered without a drain hook must not be silently
            # dropped at stop (AR-110). Default to a generic queue.join() drain —
            # the same behavior every built-in backend's drain implements — so a
            # drain-less custom backend still flushes its queue and reports a
            # status result instead of losing records at every shutdown.
            async def default_drain(timeout: float) -> BackendDrainResult:
                try:
                    await asyncio.wait_for(queue.join(), timeout=timeout)
                except asyncio.TimeoutError:
                    return BackendDrainResult(name, DrainStatus.TIMEOUT)
                except Exception as exc:
                    return BackendDrainResult(name, DrainStatus.ERROR, exc)
                else:
                    return BackendDrainResult(name, DrainStatus.COMPLETED)

            drain = default_drain
        self._drain_hooks.append(drain)
```

Update the `register_backend` docstring (lines 209-232): change "optionally by a `drain` hook" to state that when `drain` is omitted a generic `queue.join()` drain is used, so a drain-less backend is never silently dropped at stop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py -q`
Expected: PASS. `test_register_backend_duplicate_name_raises` (`:201-211`) registers a drain-less backend and only asserts the duplicate guard — still passes. `test_basic_handler.py:286` asserts `handler._drain_hooks == [backend.drain]` for the console (which passes an explicit drain) — still passes.

- [ ] **Step 5: Update `docs/architecture/components.md`** — in the `register_backend` description (lines 79-84), state that a missing `drain` defaults to a generic `queue.join()` drain.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_async_logging_handler.py docs/architecture/components.md
git commit -m "fix: default register_backend to a queue.join drain (AR-110)"
```

---

## Task 4: AR-115 — Expose a public `worker` property on `ConsoleBackend`

**Files:**
- Modify: `src/scietex/logging/console_backend.py` (add `worker` property)
- Modify: `src/scietex/logging/basic_handler.py:91` (use `backend.worker`)
- Test: `tests/test_console_backend.py`, `tests/test_basic_handler.py`
- Docs: `docs/architecture/components.md`

**Interfaces:**
- Consumes: `ConsoleBackend._worker` (the existing coroutine method).
- Produces: `ConsoleBackend.worker` — read-only property returning the bound `_worker` method (a zero-arg callable usable as a worker factory). `AsyncBaseHandler` registers `backend.worker` instead of `backend._worker`.

**Design decision — property returning the bound method, keep `_worker`:** `_worker` is the worker coroutine and is called directly by tests (`test_console_backend.py:66,85,103,120,149,187,207`). Renaming it would break those tests and is unnecessary churn. A read-only `worker` property returning the bound `_worker` gives a public registration seam while keeping `_worker` intact. `basic_handler.py` switches to `backend.worker`.

- [ ] **Step 1: Write the failing test** (in `tests/test_console_backend.py`, after `test_worker_reports_via_module_logger_when_no_error_handler`)

```python
def test_worker_property_exposes_bound_worker():
    """worker is a public read-only accessor for the worker coroutine (AR-115)."""
    running_event = asyncio.Event()
    backend = ConsoleBackend(lambda: FakeFormatter(), running_event)

    assert backend.worker is backend._worker  # same bound method
    assert callable(backend.worker)
    with pytest.raises(AttributeError):
        backend.worker = None  # read-only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_console_backend.py::test_worker_property_exposes_bound_worker -v`
Expected: FAIL with `AttributeError: 'ConsoleBackend' object has no attribute 'worker'`

- [ ] **Step 3: Add the property** in `console_backend.py`, after `__init__` (line 100):

```python
    @property
    def worker(self) -> Callable[[], Coroutine[Any, Any, None]]:
        """Public worker-factory accessor for registration (AR-115).

        Returns the bound ``_worker`` coroutine method so ``AsyncBaseHandler``
        and custom integrators can register this backend's worker without
        reaching into a private attribute.
        """
        return self._worker
```

Add the needed imports to `console_backend.py` (line 13): `from collections.abc import Awaitable, Callable, Coroutine` and `from typing import Any`. (Check current imports: line 13 is `from collections.abc import Callable`; extend it.)

- [ ] **Step 4: Update `basic_handler.py:91`** to use the public accessor:

```python
            self.register_backend(
                "console",
                self._console_backend.queue,
                self._console_backend.worker,
                self._console_backend.drain,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_console_backend.py tests/test_basic_handler.py -q`
Expected: PASS. `test_basic_handler.py:283` asserts `len(handler.log_worker_factories) == 1` — the registered factory is now `backend.worker` (== `backend._worker`), so the count is unchanged.

- [ ] **Step 6: Update `docs/architecture/components.md`** — in the `ConsoleBackend` public-interface list (lines 153-170), add the `worker` property; in the `AsyncBaseHandler` section (lines 192-196), note it registers `backend.worker`.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/console_backend.py src/scietex/logging/basic_handler.py tests/test_console_backend.py docs/architecture/components.md
git commit -m "refactor: expose public worker accessor on ConsoleBackend (AR-115)"
```

---

## Task 5: Documentation — AR-109, AR-111, AR-112, AR-106 (docs only)

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/advanced.md`
- Modify: `docs/architecture/components.md`

**Interfaces:** none (docs only). No code, no tests.

**Scope of each doc finding:**

- **AR-109 (reserved backend names):** `AsyncBrokerHandler(queue_name="console")` with default `stdout_enable=True` raises `ValueError` at construction because `basic_handler.py:88-89` already registered `"console"`. Document `"console"`, `"redis"`, `"valkey"` as reserved queue names for the extension point. (Namespacing the console name is rejected: it would change the public `log_queues["console"]` key that tests and docs rely on, and the loud `ValueError` from the AR-028 duplicate guard is already the correct failure mode — the gap is only that the reserved names aren't documented.)
- **AR-111 (formatter must not mutate the record):** the shared `LogRecord` fan-out (`async_logging_handler.py:327-336`) is safe only because `ScietexFormatter.format` copies the record (`formatter.py:87`). A user formatter that mutates `record` in place leaks changes across backends. Document that formatters must copy the record before mutating it (or not mutate it). (Defensive per-backend copies at emit time are rejected: they add allocation on the hot path for a latent risk that only a misbehaving user formatter triggers, and the shipped formatter is already safe.)
- **AR-112 (sync error_handler):** `_report_error` invokes `config.error_handler` synchronously on the producer's thread inside `emit` (`async_logging_handler.py:334,336`). Document that the callback must be fast and non-blocking. (Deferring to a worker is rejected: it adds machinery for a LOW-severity, error-path-only concern, and the review rates it LOW.)
- **AR-106 (broker inherits console-by-default):** `AsyncBrokerHandler` extends `AsyncBaseHandler` (`message_broker_handler.py:25`), so every broker backend attaches a console sink unless `stdout_enable=False`. Document the default. (Composition/rename is rejected as a large breaking change; the review itself says "at least document the default.")

- [ ] **Step 1: Document reserved names + formatter non-mutation + sync error_handler in `docs/configuration.md`**

In the "Console Logging Control" section (after line 70, the `AsyncLoggingHandler` subclass example), add a reserved-names note:

```markdown
### Reserved backend names

`register_backend` rejects a duplicate queue name with `ValueError` (AR-028).
The built-in backends reserve these names, so a custom `AsyncBrokerHandler`
subclass must not use them as its `queue_name`:

- `"console"` — registered by `AsyncBaseHandler` when `stdout_enable=True`
  (the default). `AsyncBrokerHandler(queue_name="console")` therefore raises at
  construction unless you pass `stdout_enable=False`.
- `"redis"` / `"valkey"` — registered by `AsyncRedisHandler` / `AsyncValkeyHandler`.

Choose a distinct `queue_name` for a custom backend (e.g. `"postgres"`, `"http"`).
```

In the "Custom Formatters" section (after the "Formatter scope" subsection, ~line 223), add a non-mutation note:

```markdown
### Formatters must not mutate the record

The same `LogRecord` is fanned out to every backend queue at emit time. The
shipped `ScietexFormatter` copies the record before formatting, so it never
mutates the shared record. A **custom formatter must do the same**: if it
mutates the record in place (e.g. `record.custom_field = ...`), the mutation
leaks to the broker worker and to any other backend that later reads the same
record. Copy the record first (`import copy; record = copy.copy(record)`) or
avoid mutating it.
```

In the "Error Handler" section (after line 118), add a sync-callback note:

```markdown
The `error_handler` callback runs **synchronously on the producer's thread**
inside `emit` (and on the worker for backend delivery failures). Keep it fast
and non-blocking — a slow callback stalls the logging call under exactly the
overload condition that triggers it.
```

- [ ] **Step 2: Document reserved names + console-by-default in `docs/advanced.md`**

In the "Custom Backends" section (after the "Required Methods" list, ~line 59), add:

```markdown
### Console-by-default and reserved names

`AsyncBrokerHandler` extends `AsyncBaseHandler`, so a custom broker backend
**attaches a console sink by default** (`stdout_enable=True`). Pass
`stdout_enable=False` for a broker-only handler (see `examples/custom_backend.py`).

The built-in backends reserve the queue names `"console"`, `"redis"`, and
`"valkey"`. Choose a distinct `queue_name` for your custom backend — using a
reserved name raises `ValueError` at construction when the console backend is
enabled.
```

- [ ] **Step 3: Update `docs/architecture/components.md`** — in the `AsyncBrokerHandler` section (lines 216-255), add a note that it inherits the console backend by default (`stdout_enable=True`) and that `"console"`/`"redis"`/`"valkey"` are reserved queue names.

- [ ] **Step 4: Verify no code changed and docs render**

Run: `uv run pytest -q` (must stay 106 passed — docs only)
Run: `uv run ruff check .` (must stay clean)

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md docs/advanced.md docs/architecture/components.md
git commit -m "docs: document reserved names, formatter non-mutation, sync error_handler, console-by-default (AR-106, AR-109, AR-111, AR-112)"
```

---

## Task 6: Full verification and final commit

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: 106 passed (no new tests added beyond the four in Tasks 1-4, so the count is 106 + 4 = 110; if any existing test was removed, adjust — the target is "all green").

Run: `uv run ruff check .`
Expected: clean.

Run: `uv run ty check src/scietex/logging/`
Expected: clean.

- [ ] **Step 2: Grep for leftover duplication**

Run: `rg -n "_error_logger|def _report_error" src/scietex/logging/`
Expected: `_error_logger` appears only in `config.py`; `_report_error` appears only as the delegating methods in `async_logging_handler.py` and `console_backend.py` (no duplicated bodies).

Run: `rg -n "service_name}:{.*worker_id|worker_name" src/scietex/logging/`
Expected: the identity f-string appears only in the `worker_name` property (`async_logging_handler.py`) and `ScietexFormatter.__init__` (`formatter.py:54`, its own standalone attribute); the broker worker reads `self.worker_name`.

- [ ] **Step 3: Confirm git state**

Run: `git status --short` and `git log --oneline -8`
Expected: only the files each task committed are staged/committed; no stray files.

---

## Self-Review

**Spec coverage:**
- AR-107 → Task 1 (code). AR-108 → Task 2 (code). AR-110 → Task 3 (code). AR-115 → Task 4 (code). AR-109/AR-111/AR-112/AR-106 → Task 5 (docs). AR-116 → Decision D1 (defer). AR-113 → already done in P2 (excluded). AR-114 → accepted (excluded). All nine in-scope findings covered.
- The review's P3 recommendation (lines 460-468) groups AR-107/AR-108 together, AR-109/AR-110 together, AR-106/AR-115/AR-116 together, AR-111/AR-112/AR-113/AR-114 as record/document. This plan implements AR-107/AR-108/AR-110/AR-115 as code, documents AR-109/AR-111/AR-112/AR-106, defers AR-116, and excludes AR-113/AR-114 — matching the task's stated working assumption.

**Placeholder scan:** every code step has concrete code; every doc step names the exact section and text to add. No TBD/TODO.

**Type consistency:** `worker_name` property (Task 1) is consumed by the broker worker (Task 1 Step 4). `report_error(handler_name, error_handler, record, exc)` (Task 2) is called from both delegating methods with matching signatures. `register_backend`'s default drain returns `BackendDrainResult(name, ...)` (Task 3) matching the existing `BackendDrainResult` dataclass. `ConsoleBackend.worker` (Task 4) returns the bound `_worker`, consumed by `basic_handler.py`. All names consistent across tasks.

**Edit-conflict / ordering check:** Tasks 1-4 all touch `async_logging_handler.py` (Task 1 adds a property; Task 2 edits `_report_error` + removes `_error_logger`; Task 3 edits `register_backend`). They are ordered so each edits a disjoint region: Task 1 adds a property near line 200; Task 2 edits the module top (line 21) and `_report_error` (465-493); Task 3 edits `register_backend` (202-238). Task 2's import-line edit (line 18) and Task 1's property addition (near 200) do not overlap Task 3's region. Tasks 1-4 are sequential (each commits before the next starts), so no concurrent-edit conflict. Task 4 edits `console_backend.py` (already edited by Task 2) and `basic_handler.py` (not edited elsewhere). Task 5 is docs-only and touches files not edited by Tasks 1-4 except `docs/architecture/components.md`, which Tasks 1-3 also touch — but all are sequential commits, so no conflict.

**Risk:** the default-drain closure in Task 3 captures `queue` and `name` from `register_backend`'s scope; because each `register_backend` call creates a fresh closure, concurrent backends each get their own drain bound to their own queue — no shared-state bug. The `default_drain` is defined inside the method, so it is not a module-level name that could collide.

---

## Handoff Plan

1. Execute Tasks 1-4 in order (each is a self-contained code change with its own test + commit). Task 1 first (identity property), then Task 2 (shared helper — touches `async_logging_handler.py` and `console_backend.py`), then Task 3 (`register_backend` default drain), then Task 4 (`ConsoleBackend.worker` + `basic_handler.py`).
2. Execute Task 5 (docs) after the code tasks so the docs describe the final code.
3. Run Task 6 full verification.
- Risk: Task 2's `config.py` helper must not create an import cycle — `config.py` imports only stdlib, and both `async_logging_handler.py` and `console_backend.py` already import from `config.py`, so adding `report_error` there is safe. Verify with `uv run ty check src/scietex/logging/`.
- Risk: Task 3's default drain changes behavior for drain-less backends from "silently dropped" to "drained + status-reported" — confirm Decision D2 (default, not require) before running, since require would be a breaking change to `register_backend`.
- Risk: Task 1's `worker_name` property must not collide with any existing handler attribute — grep confirms none exists; the default formatter already derives from config so no behavior change.
- Test: after each task, `uv run pytest -q` stays green; after Task 6, `uv run ruff check .` and `uv run ty check src/scietex/logging/` are clean.
