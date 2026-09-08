# Remove `formatter` from Broker & Machinery Handlers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `formatter=` a console/file-only constructor option by removing it from the pure-machinery base `AsyncLoggingHandler` and from all broker handlers (`AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`), moving formatter construction/storage down into `ConsoleHandler` and `AsyncFileHandler` (+ its 3 rotation variants).

**Architecture:** The base `AsyncLoggingHandler` becomes pure machinery with no formatter concept (it never calls `.format()`). `ConsoleHandler` and `AsyncFileHandler` (the only handlers that render text via a formatter) each own their own `self.formatter` (defaulting to `ScietexFormatter`) and pass `lambda: self.formatter` to their backend. Broker handlers drop the parameter entirely — their wire payloads are built from the record directly.

**Tech Stack:** Python 3.10+, asyncio, stdlib `logging`, pytest, ruff.

**Spec:** This implements ROADMAP "Open question 3" option (a) — `docs/ROADMAP.md:166-178`. Design decision already resolved by the orchestrator (Option B in the task brief). Hard breaking change folding into the single `[2.0.0]` changelog entry (`CHANGELOG.md:8-39`).

## Global Constraints

- **No new dependencies.** Pure stdlib + existing package modules only.
- **`ScietexFormatter` default preserved** for console and file handlers: `self.formatter = formatter if formatter is not None else ScietexFormatter()`.
- **`setFormatter` must keep working** on console/file handlers (stdlib `logging.Handler.setFormatter` sets `self.formatter`, which those handlers now own). On broker handlers `setFormatter` sets an unused attribute — acceptable; document it.
- **Do NOT touch** historical planning/review docs under `docs/superpowers/plans/*.md` and `docs/reviews/*.md` — they are immutable records of past work.
- **`docs/api/handlers.rst`** uses autodoc (`.. autoclass::`) so signatures update automatically from docstrings — no manual edit.
- **`docs/conf.py`** MyST label `(formatter-scope)=` in `configuration.md` is referenced by `{ref}` in `advanced.md` and a link in `README.md` — keep the label target intact when editing that section.
- **Version floor:** Python >=3.10 (per `pyproject.toml`). Type annotations use `X | None` syntax.
- **Every public method needs a caller** — no dead code after the refactor.

---

## File Structure

**Source (Phase 1):**
- `src/scietex/logging/async_logging_handler.py` — remove `formatter` param, `self.formatter` storage, `ScietexFormatter` import, formatter docstring lines.
- `src/scietex/logging/handler/console.py` — add `formatter` param + `self.formatter` storage + `ScietexFormatter` import; stop forwarding to super.
- `src/scietex/logging/handler/file.py` — add `formatter` param + `self.formatter` storage to `AsyncFileHandler` and its 3 rotation variants; stop forwarding to super.
- `src/scietex/logging/handler/broker.py` — remove `formatter` param + forwarding; update docstring.
- `src/scietex/logging/handler/redis.py`, `valkey.py`, `mqtt.py` — remove `formatter` param + forwarding + "no visible effect" docstring block.

**Tests (Phase 2):**
- `tests/test_async_logging_handler.py` — rework the two base-formatter tests.
- `tests/test_message_broker_handler.py` — rework/remove the broker-formatter-invariance test.
- `tests/test_console_handler.py`, `tests/test_file_handler.py` — verify unchanged tests still pass.

**Examples (Phase 3):**
- `examples/pure_machinery_handler.py` — **must change**: subclasses `AsyncLoggingHandler` and calls `self.formatter.format(record)` (line 26), which breaks when the base loses `formatter`.
- `examples/custom_formatter.py` — comment-only update (optional).

**Docs (Phase 4):**
- `docs/ROADMAP.md`, `docs/configuration.md`, `docs/advanced.md`, `README.md`, `AGENTS.md`, `docs/architecture/{components,data-flow,hotspots,overview,structure,dependencies,lifecycle}.md`, `docs/examples.md`, `CHANGELOG.md`.

---

## Phase 1 — Source

### Task 1: Strip `formatter` from the machinery base `AsyncLoggingHandler`

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:26, 96-97, 131-138, 157-159, 170`

**Interfaces:**
- Consumes: nothing new.
- Produces: `AsyncLoggingHandler.__init__(*, error_handler=None, queue_maxsize=10000, backend_config=None)` — no `formatter` kwarg, no `self.formatter` attribute. Subclasses that render text (console/file) must now own their own `self.formatter`.

- [ ] **Step 1: Remove the `ScietexFormatter` import**

At `async_logging_handler.py:26`, delete the line:
```python
from .formatter.scietex import ScietexFormatter
```
Verify nothing else in the file references `ScietexFormatter` (only line 170 does, removed in Step 4).

- [ ] **Step 2: Remove the formatter attribute docstring**

At `async_logging_handler.py:96-97`, delete the two lines:
```python
        formatter (logging.Formatter): Formatter used to render records; the
            default ``ScietexFormatter`` unless a custom one was injected.
```

- [ ] **Step 3: Remove the `formatter` parameter from `__init__`**

At `async_logging_handler.py:137`, delete the line:
```python
formatter: logging.Formatter | None = (None,)
```
At `async_logging_handler.py:157-159`, delete the three docstring lines:
```python
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed.
```

- [ ] **Step 4: Remove `self.formatter` storage**

At `async_logging_handler.py:170`, delete the line:
```python
        self.formatter = formatter if formatter is not None else ScietexFormatter()
```

- [ ] **Step 5: Verify the base no longer references formatter**

Run: `rg -n "formatter|ScietexFormatter" src/scietex/logging/async_logging_handler.py`
Expected: no matches (the file is now formatter-free).

- [ ] **Step 6: Run the machinery tests (expect the two formatter tests to fail)**

Run: `uv run pytest tests/test_async_logging_handler.py -v`
Expected: `test_default_formatter_is_scietex_formatter` and `test_custom_formatter_injected_at_construction` FAIL (they read `BareHandler().formatter`); all other tests PASS. These two are reworked in Task 5.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py
git commit -m "refactor: drop formatter from AsyncLoggingHandler machinery base"
```

---

### Task 2: Give `ConsoleHandler` its own `formatter`

**Files:**
- Modify: `src/scietex/logging/handler/console.py:8-13, 34, 48-50, 58-63`

**Interfaces:**
- Consumes: `AsyncLoggingHandler.__init__` without `formatter` (Task 1).
- Produces: `ConsoleHandler.__init__(*, error_handler=None, queue_maxsize=10000, backend_config=None, formatter=None)` — stores `self.formatter` (default `ScietexFormatter`), passes `lambda: self.formatter` to `ConsoleBackend`. `setFormatter` (stdlib) keeps working because `self.formatter` is now owned here.

- [ ] **Step 1: Add the `ScietexFormatter` import**

At `handler/console.py:11-13`, add an import line after the existing `from ..async_logging_handler import AsyncLoggingHandler`:
```python
from ..formatter.scietex import ScietexFormatter
```

- [ ] **Step 2: Keep the `formatter` param but stop forwarding it to super**

The `formatter: logging.Formatter | None = None` param at `handler/console.py:34` stays. At `handler/console.py:58-63`, change the `super().__init__(...)` call to drop the `formatter=formatter,` line:
```python
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_config,
        )
```

- [ ] **Step 3: Store `self.formatter` before constructing the backend**

Insert after the `super().__init__(...)` call (before the `self._console_backend = ConsoleBackend(...)` at line 64):
```python
        self.formatter = formatter if formatter is not None else ScietexFormatter()
```
The existing `lambda: self.formatter` at `handler/console.py:65` now reads this handler-owned attribute — no change needed there.

- [ ] **Step 4: Update the docstring**

At `handler/console.py:48-50`, the `formatter` arg docstring stays accurate ("Defaults to None, in which case a default ``ScietexFormatter`` is constructed") — no change needed. Optionally add a note that `formatter` is console-specific.

- [ ] **Step 5: Run the console tests**

Run: `uv run pytest tests/test_console_handler.py -v`
Expected: all PASS, including `test_set_formatter_propagates_to_console_backend` (line 125) and `test_set_formatter_console_reads_dynamically` (line 153), which exercise `setFormatter` + `formatter_provider()`.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/handler/console.py
git commit -m "refactor: ConsoleHandler owns its formatter instead of the machinery base"
```

---

### Task 3: Give `AsyncFileHandler` and its 3 rotation variants their own `formatter`

**Files:**
- Modify: `src/scietex/logging/handler/file.py:21-23, 50-53, 66, 86-88, 94-98, 265, 282, 284-294, 401, 420, 422-432, 537, 551, 553-563`

**Interfaces:**
- Consumes: `AsyncLoggingHandler.__init__` without `formatter` (Task 1).
- Produces: `AsyncFileHandler(filename=None, *, ..., formatter=None)` and the 3 rotation variants each accept `formatter` and store `self.formatter` (default `ScietexFormatter`). The class annotation `formatter: logging.Formatter` at line 53 stays. Each worker already reads `self.formatter.format(record)` (lines 206, 360, 500, 613) — those keep working because `self.formatter` is now owned here.

- [ ] **Step 1: Add the `ScietexFormatter` import**

At `handler/file.py:21-23`, add an import line after the existing `from ..async_logging_handler import ...`:
```python
from ..formatter.scietex import ScietexFormatter
```

- [ ] **Step 2: Update the class-level comment and annotation**

At `handler/file.py:50-53`, the comment says "Always non-None: AsyncLoggingHandler.__init__ installs a default ScietexFormatter when no formatter is passed". Rewrite it to reference the handler's own storage:
```python
    # Always non-None: __init__ installs a default ScietexFormatter when no
    # formatter is passed, so the worker can format records without a None guard.
    formatter: logging.Formatter
```

- [ ] **Step 3: `AsyncFileHandler` — stop forwarding `formatter` to super, store it**

At `handler/file.py:94-98`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
```
Insert after the `super().__init__(...)` call (before the `if file is not None and filename is not None:` check at line 99):
```python
        self.formatter = formatter if formatter is not None else ScietexFormatter()
```
The `formatter` param at line 66 and its docstring at lines 86-88 stay. The `lambda: self.formatter` at line 113 now reads the handler-owned attribute — no change needed.

- [ ] **Step 4: `AsyncRotatingFileHandler` — stop forwarding, rely on inherited storage**

`AsyncRotatingFileHandler` subclasses `AsyncFileHandler`, so it inherits `self.formatter` storage from `AsyncFileHandler.__init__`. At `handler/file.py:284-294`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
```
The `formatter` param at line 265 and its docstring at line 282 stay (they flow through to `AsyncFileHandler.__init__`).

- [ ] **Step 5: `AsyncTimedRotatingFileHandler` — stop forwarding**

At `handler/file.py:422-432`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            filename,
            mode="a",
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
```
The `formatter` param at line 401 and its docstring at line 420 stay.

- [ ] **Step 6: `AsyncWatchedFileHandler` — stop forwarding**

At `handler/file.py:553-563`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            filename,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
            file=file,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
```
The `formatter` param at line 537 and its docstring at line 551 stay.

- [ ] **Step 7: Run the file handler tests**

Run: `uv run pytest tests/test_file_handler.py -v`
Expected: all PASS, including `test_file_handler_json_formatter` (line 90) which passes `formatter=JsonFormatter()`.

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/handler/file.py
git commit -m "refactor: file handlers own their formatter instead of the machinery base"
```

---

### Task 4: Remove `formatter` from broker handlers

**Files:**
- Modify: `src/scietex/logging/handler/broker.py:65, 84-86, 96-101`
- Modify: `src/scietex/logging/handler/redis.py:47, 68-71, 90-97`
- Modify: `src/scietex/logging/handler/valkey.py:47, 70-73, 90-97`
- Modify: `src/scietex/logging/handler/mqtt.py:53, 81-84, 101-108`

**Interfaces:**
- Consumes: `AsyncLoggingHandler.__init__` without `formatter` (Task 1).
- Produces: `AsyncBrokerHandler.__init__(queue_name, *, error_handler=None, queue_maxsize=10000, backend_config=None, client=None)` — no `formatter`. `AsyncRedisHandler(stream_name, *, redis_config=None, client=None, error_handler=None, queue_maxsize=10000)`, `AsyncValkeyHandler(...)`, `AsyncMqttHandler(...)` likewise. Passing `formatter=` to any broker handler now raises `TypeError`.

- [ ] **Step 1: `AsyncBrokerHandler` — remove param, forwarding, docstring**

At `handler/broker.py:65`, delete the line:
```python
formatter: logging.Formatter | None = (None,)
```
At `handler/broker.py:84-86`, delete the three docstring lines:
```python
            formatter (logging.Formatter | None): Formatter used to render records.
                Defaults to None, in which case a default ``ScietexFormatter`` is
                constructed.
```
At `handler/broker.py:96-101`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_config,
        )
```

- [ ] **Step 2: `AsyncRedisHandler` — remove param, forwarding, docstring**

At `handler/redis.py:47`, delete the line:
```python
formatter: logging.Formatter | None = (None,)
```
At `handler/redis.py:68-71`, delete the four docstring lines:
```python
            formatter (logging.Formatter | None): Formatter used to render records.
                Has no visible effect on broker payloads; broker output is built from
                the record directly. Defaults to None, in which case a default
                ``ScietexFormatter`` is constructed.
```
At `handler/redis.py:90-97`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            queue_name="redis",
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
        )
```

- [ ] **Step 3: `AsyncValkeyHandler` — remove param, forwarding, docstring**

At `handler/valkey.py:47`, delete the line:
```python
formatter: logging.Formatter | None = (None,)
```
At `handler/valkey.py:70-73`, delete the four docstring lines:
```python
            formatter (logging.Formatter | None): Formatter used to render records.
                Has no visible effect on broker payloads; broker output is built from
                the record directly. Defaults to None, in which case a default
                ``ScietexFormatter`` is constructed.
```
At `handler/valkey.py:90-97`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            queue_name="valkey",
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
        )
```

- [ ] **Step 4: `AsyncMqttHandler` — remove param, forwarding, docstring**

At `handler/mqtt.py:53`, delete the line:
```python
formatter: logging.Formatter | None = (None,)
```
At `handler/mqtt.py:81-84`, delete the four docstring lines:
```python
            formatter (logging.Formatter | None): Formatter used to render records.
                Has no visible effect on broker payloads; broker output is built from
                the record directly. Defaults to None, in which case a default
                ``ScietexFormatter`` is constructed.
```
At `handler/mqtt.py:101-108`, change the `super().__init__(...)` call to drop `formatter=formatter,`:
```python
        super().__init__(
            queue_name="mqtt",
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
        )
```

- [ ] **Step 5: Verify no broker handler references formatter**

Run: `rg -n "formatter" src/scietex/logging/handler/broker.py src/scietex/logging/handler/redis.py src/scietex/logging/handler/valkey.py src/scietex/logging/handler/mqtt.py`
Expected: only the `_worker` comment at `broker.py:225-228` ("not from formatter internals ... custom formatter/datefmt") remains — that comment is still accurate (it explains why broker fields derive from the record) and should be kept. No `formatter=` param, no `formatter=formatter,` forwarding, no `formatter (` docstring.

- [ ] **Step 6: Run the broker tests**

Run: `uv run pytest tests/test_message_broker_handler.py -v`
Expected: `test_broker_payload_invariant_under_plain_formatter` FAILS (it calls `handler.setFormatter(...)` at line 309 — see Task 6); all other broker tests PASS. Note: `setFormatter` on a broker handler still works (stdlib sets `self.formatter`, now an unused attribute) so the test fails only because its premise is obsolete, not because of a crash.

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/handler/broker.py src/scietex/logging/handler/redis.py src/scietex/logging/handler/valkey.py src/scietex/logging/handler/mqtt.py
git commit -m "refactor: remove formatter from broker handlers (wire payload is record-derived)"
```

---

## Phase 2 — Tests

### Task 5: Rework the base-formatter tests in `test_async_logging_handler.py`

**Files:**
- Modify: `tests/test_async_logging_handler.py:10, 167-181`

**Interfaces:**
- Consumes: `AsyncLoggingHandler` no longer has `formatter` (Task 1).
- Produces: Two tests that assert the base is formatter-free, replacing the two that asserted the base owned a formatter.

- [ ] **Step 1: Replace `test_default_formatter_is_scietex_formatter`**

At `tests/test_async_logging_handler.py:167-173`, replace the whole test with one asserting the base owns no formatter:
```python
def test_base_owns_no_formatter():
    """The pure-machinery base has no formatter concept (console/file own it)."""
    handler = BareHandler()

    assert not hasattr(handler, "formatter")
```

- [ ] **Step 2: Replace `test_custom_formatter_injected_at_construction`**

At `tests/test_async_logging_handler.py:176-181`, replace the whole test with one asserting `formatter=` is rejected on the base:
```python
def test_base_rejects_formatter_kwarg():
    """formatter= is console/file-specific; the machinery base rejects it (TypeError)."""
    with pytest.raises(TypeError):
        BareHandler(formatter=logging.Formatter("%(message)s"))
```

- [ ] **Step 3: Remove the now-unused `ScietexFormatter` import**

At `tests/test_async_logging_handler.py:10`, the import `from scietex.logging import AsyncLoggingHandler, ScietexFormatter` — remove `ScietexFormatter` (no longer referenced after Steps 1-2):
```python
from scietex.logging import AsyncLoggingHandler
```

- [ ] **Step 4: Run the machinery tests**

Run: `uv run pytest tests/test_async_logging_handler.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_async_logging_handler.py
git commit -m "test: base machinery owns no formatter; formatter= rejected on it"
```

---

### Task 6: Rework the broker-formatter-invariance test in `test_message_broker_handler.py`

**Files:**
- Modify: `tests/test_message_broker_handler.py:300-320`

**Interfaces:**
- Consumes: broker handlers no longer accept `formatter` (Task 4).
- Produces: A test asserting broker payload invariance is preserved even when `setFormatter` is called (documenting that `setFormatter` on a broker handler is a harmless no-op on the wire payload).

- [ ] **Step 1: Rework `test_broker_payload_invariant_under_plain_formatter`**

At `tests/test_message_broker_handler.py:300-320`, the test's premise (broker accepts a formatter that could affect output) is obsolete. The payload-invariance property is still worth locking in, but the mechanism changed: broker handlers no longer accept `formatter=`, and `setFormatter` now sets an unused attribute. Rework the test to assert that calling `setFormatter` (the only remaining way a formatter could reach a broker handler) still leaves the wire payload unchanged:

```python
@pytest.mark.asyncio
async def test_broker_payload_invariant_under_set_formatter():
    """Broker name/time derive from the record; setFormatter cannot change the payload."""
    handler = FakeBrokerHandler(
        queue_name="broker",
    )
    # Broker handlers no longer accept formatter=; setFormatter (stdlib) sets an
    # unused attribute. The wire payload must still be record-derived.
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))

    await handler.start_logging()
    record = _make_record("hello")
    handler.emit(record)

    await _wait_for(lambda: bool(handler.sent))
    entry = handler.sent[0]
    assert entry["name"] == "TestLogger"
    assert entry["time"] == datetime.fromtimestamp(record.created, timezone.utc).isoformat()

    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 2: Run the broker tests**

Run: `uv run pytest tests/test_message_broker_handler.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_message_broker_handler.py
git commit -m "test: broker payload stays record-derived under setFormatter"
```

- [ ] **Step 4: Grep for any other test reading `.formatter` on a broker handler or the base**

Run: `rg -n "\.formatter|formatter=" tests/`
Expected: remaining matches are only in `test_console_handler.py` (lines 128-135, 162 — console owns formatter, correct), `test_file_handler.py` (line 95 — file owns formatter, correct), `test_file_backend.py`/`test_console_backend.py` (formatter_provider tests, correct), `test_formatter.py`/`test_json_formatter.py` (formatter unit tests, correct). No broker or base `.formatter` reads remain.

---

## Phase 3 — Examples

### Task 7: Fix `examples/pure_machinery_handler.py` (breaks when base loses formatter)

**Files:**
- Modify: `examples/pure_machinery_handler.py:10-34`

**Interfaces:**
- Consumes: `AsyncLoggingHandler` no longer has `self.formatter` (Task 1).
- Produces: A working example of a custom backend on the pure machinery base that owns its own formatter.

- [ ] **Step 1: Give `FileLikeHandler` its own formatter**

`FileLikeHandler` subclasses `AsyncLoggingHandler` directly and calls `self.formatter.format(record)` at line 26 — this breaks when the base loses `formatter`. Add a `formatter` param + storage to the subclass `__init__`:

```python
class FileLikeHandler(AsyncLoggingHandler):
    """Handler that formats records into an in-memory list of lines."""

    def __init__(self, *args, formatter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.formatter = formatter if formatter is not None else ScietexFormatter()
        self.written: list[str] = []
        # AsyncLoggingHandler registers no backend itself; this handler registers a
        # single "filelike" backend whose worker formats records into self.written.
        self.register_backend("filelike", asyncio.Queue(), self._worker, self.drain)
```

- [ ] **Step 2: Add the `ScietexFormatter` import**

At `examples/pure_machinery_handler.py:6`, change the import to include `ScietexFormatter`:
```python
from scietex.logging import AsyncLoggingHandler, ScietexFormatter
```

- [ ] **Step 3: Run the example**

Run: `uv run python examples/pure_machinery_handler.py`
Expected: prints the two formatted lines ("Info message through the custom backend." / "Error message through the custom backend.") with no `AttributeError`.

- [ ] **Step 4: Commit**

```bash
git add examples/pure_machinery_handler.py
git commit -m "fix: pure-machinery example owns its formatter after base refactor"
```

- [ ] **Step 5: Verify no other example passes `formatter=` to a broker handler**

Run: `rg -n "formatter=" examples/`
Expected: only `examples/file_logging.py:24` (`AsyncFileHandler(..., formatter=JsonFormatter())` — correct, file owns formatter). No broker handler in any example passes `formatter=`.

- [ ] **Step 6: Update the comment in `examples/custom_formatter.py` (optional but recommended)**

At `examples/custom_formatter.py:24-28`, the comment says "setFormatter replaces the handler's formatter for the console (stdout) sink only. Broker backends build their payloads from the record directly ... so they are invariant under setFormatter." This is still accurate but can be tightened to note broker handlers no longer accept `formatter=` at all. Update lines 24-28 to:
```python
    # setFormatter replaces the handler's formatter for the console (stdout)
    # sink only. Broker handlers no longer accept a formatter at all — their
    # payloads are built from the record directly (including record.name). This
    # example uses ConsoleHandler, whose only sink is the console, so the
    # custom layout appears in its output.
    handler.setFormatter(formatter)
```

- [ ] **Step 7: Commit**

```bash
git add examples/custom_formatter.py
git commit -m "docs: clarify broker handlers no longer accept formatter in example"
```

---

## Phase 4 — Docs

### Task 8: Mark ROADMAP Open question 3 resolved

**Files:**
- Modify: `docs/ROADMAP.md:166-178`

- [ ] **Step 1: Mark Open question 3 as resolved**

At `docs/ROADMAP.md:166-178`, rewrite the "Open question 3" block to record the resolution (option (a) chosen in 2.0.0):

```markdown
### Open question 3 — Formatter scope on broker handlers (resolved in 2.0.0)

- **Problem:** `formatter=`/`setFormatter` affects console output only; broker
  payloads are built from config + record and are invariant under the formatter
  (AR-104, AR-018). The `formatter=` kwarg was threaded through every handler,
  so on a broker handler it was dead weight — the broker has no console sink to
  render it (the console sink now lives in `ConsoleHandler`).
- **Resolution (2.0.0):** option (a) — `formatter=` was removed from the
  pure-machinery base `AsyncLoggingHandler` and from all broker handlers
  (`AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`,
  `AsyncMqttHandler`). `ConsoleHandler` and `AsyncFileHandler` (and its rotation
  variants) now own their own `formatter`, defaulting to `ScietexFormatter`.
  Passing `formatter=` to a broker handler or the base now raises `TypeError`.
```

- [ ] **Step 2: Update the AR-116 reference to the base accepting formatter**

At `docs/ROADMAP.md:152`, the AR-116 problem statement says the base "accepts `backend_config` (broker) and `formatter`, storing them on `config` without acting on them." Since `formatter` is now removed from the base, update line 152 to reference only `backend_config`:
```markdown
  base — accepts `backend_config` (broker), storing it on `config` without
  acting on it. Every subclass re-declares the full
```

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: mark formatter-scope open question resolved in 2.0.0"
```

---

### Task 9: Update `docs/configuration.md` formatter-scope section

**Files:**
- Modify: `docs/configuration.md:331-352`

- [ ] **Step 1: Rewrite the "Formatter scope" section**

At `docs/configuration.md:331-352`, the section currently says a formatter "affects console (stdout) output only" and that broker handlers accept `formatter=`/`setFormatter` but it has no visible effect. Rewrite to reflect that broker handlers no longer accept `formatter=` at all. **Keep the `(formatter-scope)=` MyST label on line 331** (referenced by `advanced.md:80` and `README.md:191`):

```markdown
(formatter-scope)=
### Formatter scope: console and file output only

A formatter — whether injected via the `formatter=` constructor keyword or
applied later with `setFormatter` — affects **console (stdout) and file output
only**. It is accepted only by `ConsoleHandler` and `AsyncFileHandler` (and its
rotation variants), which render text through it.

Broker handlers (`AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`,
and any `AsyncBrokerHandler` subclass) do **not** accept a `formatter=` keyword
— passing one raises `TypeError`. They build their wire record from the log
record directly — its `name` field is the record's logger name (`record.name`) —
producing a fixed schema (`level`, `message`, `name`, `time`). That payload is
**invariant** under `setFormatter`, so the broker output is deterministic and
independent of any formatter you install.

Consequences to be aware of:

- On a broker handler, `setFormatter` (inherited from the stdlib `logging.Handler`)
  sets an unused attribute — it has **no visible effect** on broker output.
  Broker handlers no longer register a console sink, and the broker builds its
  payload independently of the formatter. Console output requires adding a
  `ConsoleHandler` to the logger, with the formatter installed there.
- If you need to change what a broker backend sends, that is a property of the
  backend's `send_message` implementation, not of the formatter.
```

- [ ] **Step 2: Commit**

```bash
git add docs/configuration.md
git commit -m "docs: formatter is console/file-only; broker handlers reject formatter="
```

---

### Task 10: Update `docs/advanced.md`, `README.md`, `AGENTS.md`, `docs/examples.md`

**Files:**
- Modify: `docs/advanced.md:76-80`
- Modify: `README.md:188-191`
- Modify: `AGENTS.md:209`
- Modify: `docs/examples.md:144`

- [ ] **Step 1: `docs/advanced.md` — tighten the invariance prose**

At `docs/advanced.md:76-80`, the text says broker payloads are "invariant under `setFormatter`/`formatter=`". Update to note broker handlers no longer accept `formatter=`:
```markdown
This record schema is **independent of the formatter**. Broker payloads are
built from the log record directly — the `name` field is the record's logger
name (`record.name`) — so they are invariant under `setFormatter`. Broker
handlers no longer accept a `formatter=` keyword (passing one raises
`TypeError`); a formatter affects the console (stdout) and file sinks only — see
{ref}`Formatter scope: console and file output only <formatter-scope>`.
```

- [ ] **Step 2: `README.md` — tighten the invariance prose**

At `README.md:188-191`, update to reflect broker handlers rejecting `formatter=`:
```markdown
A formatter affects the **console (stdout) and file output only**; broker
handlers no longer accept a `formatter=` keyword and build their payloads from
the log record directly — its `name` field is the record's logger name
(`record.name`) — so they are invariant under `setFormatter`. See
[docs/configuration.md](docs/configuration.md#formatter-scope-console-and-file-output-only).
```

- [ ] **Step 3: `AGENTS.md` — fix the stale "Backends share the same formatter" note**

At `AGENTS.md:209`, replace the stale line:
```markdown
- **Backends share the same formatter**: All handlers use the configured formatter
```
with:
```markdown
- **Formatter is console/file-specific**: Only `ConsoleHandler` and `AsyncFileHandler` (and its rotation variants) accept a `formatter=` keyword and render records through it (default `ScietexFormatter`). Broker handlers and the pure-machinery base `AsyncLoggingHandler` do not accept `formatter=` — broker wire payloads are built from the record directly.
```

- [ ] **Step 4: `docs/examples.md` — fix the stale "every sink" claim**

At `docs/examples.md:144`, replace:
```markdown
- `handler.setFormatter()` replaces the formatter for every sink
```
with:
```markdown
- `handler.setFormatter()` replaces the formatter for the handler's console/file sink
```

- [ ] **Step 5: Commit**

```bash
git add docs/advanced.md README.md AGENTS.md docs/examples.md
git commit -m "docs: formatter is console/file-only across guides"
```

---

### Task 11: Update architecture docs

**Files:**
- Modify: `docs/architecture/components.md:101-102, 108-110, 166, 341-344, 376, 444-448, 501-509, 539-546, 578-583`
- Modify: `docs/architecture/data-flow.md:59-63`
- Modify: `docs/architecture/hotspots.md:17-19, 268-272`
- Modify: `docs/architecture/overview.md:34`
- Modify: `docs/architecture/structure.md:49`
- Modify: `docs/architecture/dependencies.md:42, 45-46`
- Modify: `docs/architecture/lifecycle.md:197`

- [ ] **Step 1: `components.md` — machinery base section**

At `docs/architecture/components.md:101-102`, remove "Owns formatter construction," from the purpose line. At lines 108-110, change the signature line to drop `formatter=None` and remove the "Constructs a default ScietexFormatter() unless a custom formatter= is injected (AR-024)" bullet. At line 166, remove `formatter` (ScietexFormatter) from "Key instance state".

- [ ] **Step 2: `components.md` — ConsoleHandler section**

At `docs/architecture/components.md:341-344`, the signature line keeps `formatter=None` but the note "Accepts an optional `formatter=` kwarg forwarded to super (AR-024)" must change to "Accepts an optional `formatter=` kwarg (default `ScietexFormatter`) stored on the handler and passed to the console backend via `formatter_provider`."

- [ ] **Step 3: `components.md` — File handler section**

At `docs/architecture/components.md:376`, the `AsyncFileHandler` signature keeps `formatter=None`. Add a note that the handler stores `self.formatter` (default `ScietexFormatter`) and passes `lambda: self.formatter` to the `FileBackend`.

- [ ] **Step 4: `components.md` — broker sections**

At `docs/architecture/components.md:444-448`, remove `formatter=None` from the `AsyncBrokerHandler` signature and delete the "Accepts an optional `formatter=` kwarg forwarded to super (AR-024)" bullet. At lines 501-509 (Redis), 539-546 (Valkey), 578-583 (MQTT), remove `formatter=None` from each signature and delete the "Accepts an optional `formatter=` kwarg (AR-024)" phrasing.

- [ ] **Step 5: `data-flow.md`**

At `docs/architecture/data-flow.md:59-63`, the text says broker fields are computed "independently of the formatter" and the wire format is "invariant under `setFormatter`". Update to note broker handlers no longer accept `formatter=`:
```markdown
**Transformations.** `LogRecord` → dict with keys `level`, `message`, `name`,
`time`. The dict fields are computed **independently** of the formatter: `level`
is derived from `record.levelno` via `level_abbreviation` (e.g. `"INF"`), and
`name`/`time` from the record itself (`record.name` and `record.created`). Broker
handlers no longer accept a `formatter=` keyword, so the broker wire format is
deterministic and independent of any formatter.
```

- [ ] **Step 6: `hotspots.md`**

At `docs/architecture/hotspots.md:17-19`, the machinery-base section says it "accepts an optional `formatter=` kwarg (defaulting to `ScietexFormatter`) so the machinery base is formatter-agnostic (AR-024)". Update to note the base no longer accepts `formatter=` (console/file own it). At lines 268-272, the broker worker section says the wire format is "invariant under `setFormatter`" — keep this (still true) but it can note broker handlers reject `formatter=`.

- [ ] **Step 7: `overview.md`, `structure.md`, `dependencies.md`, `lifecycle.md`**

- `overview.md:34`: remove "formatter construction," from the machinery-base layer description.
- `structure.md:49`: change `async_logging_handler.py → formatter/scietex.py, config.py` to `async_logging_handler.py → config.py` (the base no longer imports the formatter). Add `handler/console.py → formatter/scietex.py` and `handler/file.py → formatter/scietex.py` to the dependency list.
- `dependencies.md:42, 45-46`: change "depends only on the stdlib and on its own formatter" to "depends only on the stdlib" and update the "Core → own ScietexFormatter" bullet to remove the formatter dependency (console/file handlers now depend on it).
- `lifecycle.md:197`: the `formatter` row says it is created in `__init__` on the handler instance. Update to note it applies only to console/file handlers (which own it), not the machinery base.

- [ ] **Step 8: Commit**

```bash
git add docs/architecture/components.md docs/architecture/data-flow.md docs/architecture/hotspots.md docs/architecture/overview.md docs/architecture/structure.md docs/architecture/dependencies.md docs/architecture/lifecycle.md
git commit -m "docs: architecture docs reflect formatter moving to console/file handlers"
```

---

### Task 12: Add the formatter removal to the CHANGELOG [2.0.0] entry

**Files:**
- Modify: `CHANGELOG.md:8-39`

- [ ] **Step 1: Add a Removed bullet**

At `CHANGELOG.md`, in the `[2.0.0]` `### Removed` section (after the `stdout_enable` bullet ending at line 29), add:
```markdown
- **`formatter` parameter**: removed from the pure-machinery base
  `AsyncLoggingHandler` and from every broker handler (`AsyncBrokerHandler`,
  `AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`). Passing
  `formatter=` to a broker handler or the base now raises `TypeError`. The
  `formatter` keyword is now console/file-specific: only `ConsoleHandler` and
  `AsyncFileHandler` (and its rotation variants) accept it, each owning its own
  `self.formatter` (default `ScietexFormatter`). Broker wire payloads are built
  from the record directly and were never affected by the formatter.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog formatter removal in 2.0.0"
```

---

## Phase 5 — Verify

### Task 13: Full verification sweep

**Files:**
- None (verification only).

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check .`
Expected: clean (no unused imports — the `ScietexFormatter` import was removed from `async_logging_handler.py` in Task 1 and added where needed in Tasks 2-3; `logging` imports in broker files remain used by the `error_handler` type annotations).

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest`
Expected: all PASS.

- [ ] **Step 3: Grep sweep for leftover `formatter=` on broker handlers or the base**

Run: `rg -n "formatter=" src/scietex/logging/`
Expected: matches only in `handler/console.py` and `handler/file.py` (the console/file `formatter` params). No matches in `async_logging_handler.py`, `handler/broker.py`, `handler/redis.py`, `handler/valkey.py`, `handler/mqtt.py`.

- [ ] **Step 4: Grep sweep for `self.formatter` reads on the base or broker**

Run: `rg -n "self\.formatter" src/scietex/logging/`
Expected: matches only in `handler/console.py` (line 65) and `handler/file.py` (lines 113, 206, 360, 500, 613) — the console/file handlers that own `self.formatter`. No matches in `async_logging_handler.py` or any broker handler.

- [ ] **Step 5: Grep sweep for `ScietexFormatter` import in the base**

Run: `rg -n "ScietexFormatter" src/scietex/logging/async_logging_handler.py`
Expected: no matches.

- [ ] **Step 6: Confirm the docs build label is intact**

Run: `rg -n "formatter-scope" docs/configuration.md docs/advanced.md README.md`
Expected: the `(formatter-scope)=` label exists in `configuration.md` and both `advanced.md` (`{ref}`) and `README.md` (anchor link) still reference it.

---

## Self-Review

**Spec coverage:** Every requirement maps to a task — base removal (Task 1), console ownership (Task 2), file ownership (Task 3), broker removal (Task 4), test rework (Tasks 5-6), example fix (Task 7), docs (Tasks 8-12), verification (Task 13). The `pure_machinery_handler.py` example breakage (a risk the brief did not flag) is handled in Task 7.

**Placeholder scan:** No TBD/TODO; every step has concrete code or exact line targets.

**Type consistency:** `AsyncLoggingHandler.__init__` loses `formatter` (Task 1); `ConsoleHandler`/`AsyncFileHandler` gain it (Tasks 2-3); broker handlers lose it (Task 4). The `ScietexFormatter` import moves from the base to console/file. Test names and assertions are consistent across Tasks 5-6.

## Handoff Plan

1. Execute Tasks 1-4 (source) in order — Task 1 must land before Tasks 2-4 (they call `super().__init__` without `formatter`).
2. Execute Tasks 5-6 (tests) — they depend on Tasks 1 and 4 respectively.
3. Execute Task 7 (example fix) — depends on Task 1.
4. Execute Tasks 8-12 (docs) — independent of source, can run after Task 4.
5. Execute Task 13 (verification) last.

- **Risk:** `examples/pure_machinery_handler.py:26` calls `self.formatter.format(record)` on a direct `AsyncLoggingHandler` subclass — it breaks the moment Task 1 lands. Fix it in the same commit batch as Task 1 or immediately after, before running the full suite.
- **Risk:** The `_NoopBrokerHandler`/`FakeBrokerHandler`/`StuckConnectBrokerHandler`/`ScriptedConnectBrokerHandler` test helpers in `test_message_broker_handler.py` and `test_console_handler.py` subclass `AsyncBrokerHandler` and pass `*args/**kwargs` through — they never pass `formatter=`, so they are unaffected. Verify with the Task 13 grep.
- **Risk:** `setFormatter` on a broker handler still works (stdlib sets `self.formatter`, now unused) — this is intentional and documented in Task 9. Do not add a `setFormatter` override to broker handlers.
- **Risk:** The `(formatter-scope)=` MyST label in `configuration.md` is cross-referenced — preserve it when rewriting the section (Task 9).
- **Test:** `uv run ruff check .` clean; `uv run pytest` all green; the Task 13 greps return no broker/base `formatter` references.
