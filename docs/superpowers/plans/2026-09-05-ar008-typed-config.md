# AR-008 Typed Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the open-ended `**kwargs` / magic-string constructor configuration with a typed, validated config object while keeping the existing public constructor API backward-compatible.

**Architecture:** Introduce a frozen `LoggingConfig` dataclass that carries the shared machinery options (`service_name`, `worker_id`, `error_handler`, `queue_maxsize`, `stdout_enable`) plus a per-backend `backend_config` slot. Each handler constructor keeps its current keyword signature but validates it by building a `LoggingConfig` internally and rejecting unknown kwargs loudly (instead of silently swallowing them at the top of the MRO chain). Backend-specific connection settings move from raw `dict`/`GlideClientConfiguration` kwargs into typed per-backend config dataclasses (`RedisConfig`, `ValkeyConfig`) that are validated and stored as `self.config`.

**Tech Stack:** Python >=3.10, stdlib `dataclasses`, `typing`, existing `asyncio`/`logging` machinery. No new dependencies.

**Spec:** `docs/reviews/architecture/2026-09-04.md` — finding AR-008 (lines 421-449), plus the migration-strategy note (lines 710-717) that typed config builds on the refactored base (AR-001/005/007, all landed).

## Global Constraints

- Python `>=3.10` (`pyproject.toml:5`). Use `X | None` union syntax and stdlib `dataclasses` only.
- **No new dependencies** (`pyproject.toml:13` has `dependencies = []`; the base package must stay zero-runtime-dependency).
- Preserve the strict one-way acyclic import graph: `formatter` → `async_logging_handler` → `basic_handler` → `message_broker_handler` → `{redis, valkey}` → `__init__`. A new `config.py` module must be imported only by modules that already depend on it (it must not import any handler module, to avoid a cycle).
- **Public constructor API stays backward-compatible.** Every existing call site in `examples/`, `docs/`, `tests/`, and `__init__.py` docstrings constructs handlers with keyword args like `AsyncBaseHandler(service_name=..., worker_id=..., stdout_enable=...)`, `AsyncRedisHandler(stream_name=..., redis_config={...})`, `AsyncValkeyHandler(stream_name=..., valkey_config=...)`. These must keep working unchanged.
- No emoji in code/comments. Comments explain WHY, not WHAT. No commented-out code. No `@ts-ignore`-style suppressions.
- Every public function/method needs at least one caller before commit.
- Scope is AR-008 only. Do **not** implement AR-010/011/012 or the backend-registration mechanism from AR-001's recommendation (that is out of scope; `queue_name` magic strings remain as registration keys for now).

---

## Grounding Summary (what was read and found)

Source files read in full: `async_logging_handler.py`, `basic_handler.py`, `message_broker_handler.py`, `redis_handler.py`, `valkey_handler.py`, `console_backend.py`, `formatter.py`, `__init__.py`; all of `tests/`; `docs/configuration.md`, `docs/backends.md`, `docs/examples.md`, `docs/architecture/{overview,components,data-flow,lifecycle}.md`; `examples/*.py`; `pyproject.toml`; the AR-008 finding text.

### Exact current constructor signatures (all keyword-only after `service_name`/`worker_id`)

- `AsyncLoggingHandler(service_name=None, worker_id=None, *, error_handler=None, queue_maxsize=10000, **kwargs)` — `async_logging_handler.py:99-107`. **The `**kwargs` are accepted and silently ignored** (`async_logging_handler.py:106,122-123`). This is the top of the MRO chain where a typo'd kwarg is silently swallowed.
- `AsyncBaseHandler(service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000, **kwargs)` — `basic_handler.py:31-40`. Forwards `**kwargs` to super (`basic_handler.py:61-67`).
- `AsyncBrokerHandler(queue_name, service_name=None, worker_id=None, **kwargs)` — `message_broker_handler.py:38-44`. Forwards `**kwargs` to super (`message_broker_handler.py:58`). `queue_name` is a **positional** param.
- `AsyncRedisHandler(stream_name, service_name=None, worker_id=None, redis_config=None, **kwargs)` — `redis_handler.py:35-42`. Forwards `**kwargs` to super (`redis_handler.py:58-63`). `redis_config` is a raw `dict` defaulting to `{"host": "localhost", "port": 6379, "db": 0}` (`redis_handler.py:65-69`).
- `AsyncValkeyHandler(stream_name, service_name=None, worker_id=None, valkey_config=None, **kwargs)` — `valkey_handler.py:35-42`. Forwards `**kwargs` to super (`valkey_handler.py:58-63`). `valkey_config` is a raw `GlideClientConfiguration` defaulting to `GlideClientConfiguration([NodeAddress()])` (`valkey_handler.py:65-69`).
- `ConsoleBackend(formatter, running_event, maxsize=10000)` — `console_backend.py:62-67`. Not a config surface; unchanged.

### Magic strings found

- Backend registration keys: `"console"` (`basic_handler.py:77`), `"redis"` (`redis_handler.py:61`), `"valkey"` (`valkey_handler.py:61`), and arbitrary user-supplied `queue_name` (`message_broker_handler.py:40,59`). These are used as `log_queues` dict keys and as the `BackendDrainResult.name` reported to the console status records. After AR-001, `stop_logging` is fully generic (no `name == "console"` special-case remains — verified by grep), so these are now **registration keys only**, not shutdown-coupling magic. Replacing them with a first-class backend-registration mechanism is AR-001's deferred recommendation and is **out of scope** here.
- Level abbreviations `DBG/INF/WRN/ERR/CRT` (`formatter.py:21-28`) and the default format string (`formatter.py:69`) are formatter internals, not handler-config magic strings. Out of scope.
- `redis_config` dict keys `host`/`port`/`db` (`redis_handler.py:65-69`) are untyped magic keys passed straight to `redis.Redis(**config)` (`redis_handler.py:83`).

### The footgun AR-008 targets

`AsyncLoggingHandler.__init__` accepts `**kwargs` and ignores them (`async_logging_handler.py:106,122-123`). Because every subclass forwards `**kwargs` up the chain, an unknown/typo'd kwarg (e.g. `stdout_enabel=True`) reaches the top and is silently dropped — no error, no warning. The fix must make unknown kwargs fail loudly at the point of construction.

### Backward-compat surface (must keep working)

- `examples/basic_console_logging.py:18` — `AsyncBaseHandler(service_name=..., worker_id=..., stdout_enable=True)`.
- `examples/console_and_redis_logging.py:19-31` — `AsyncBaseHandler(...)` and `AsyncRedisHandler(stream_name=..., service_name=..., worker_id=2, redis_config={...}, stdout_enable=True)`.
- `examples/redis_logging.py:18-23` — `AsyncRedisHandler(stream_name=..., service_name=..., worker_id=2, redis_config={...})`.
- `examples/valkey_logging.py:14-18` — `AsyncValkeyHandler(stream_name=..., service_name=..., worker_id=3)`.
- `docs/configuration.md`, `docs/backends.md`, `__init__.py` docstrings, and all tests construct handlers with these kwargs.
- Tests construct broker handlers with `queue_name="broker"` as a **positional** arg (`tests/test_basic_handler.py:211`, `tests/test_message_broker_handler.py:76,101,126,140`, `tests/test_queue_bounds.py:56,79,98,117,147,183,191`, `tests/test_restartable_lifecycle.py:103,131,182,208`).

---

## Target Design

### New module: `src/scietex/logging/config.py`

A leaf module with **no imports from any handler module** (only stdlib `dataclasses`, `typing`, `logging`), so it cannot create an import cycle. It defines the typed config objects and a small validation helper.

```python
"""Typed configuration objects for scietex.logging handlers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LoggingConfig:
    """Shared machinery options for every handler.

    Attributes:
        service_name (str): Service name used in the formatter's worker_name.
        worker_id (int): Worker id used in the formatter's worker_name.
        error_handler (Callable | None): Delivery-error callback ``(record, exc)``.
        queue_maxsize (int): Bound for every backend queue (default 10000).
        stdout_enable (bool): Whether AsyncBaseHandler registers the console backend.
        backend_config (Any | None): Backend-specific config (RedisConfig, ValkeyConfig,
            or None for the pure-machinery/console-only handlers).
    """

    service_name: str = "Service"
    worker_id: int = 1
    error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None
    queue_maxsize: int = 10000
    stdout_enable: bool = True
    backend_config: Any | None = None


@dataclass(frozen=True)
class RedisConfig:
    """Connection settings for the Redis backend.

    Attributes:
        host (str): Redis server host (default "localhost").
        port (int): Redis server port (default 6379).
        db (int): Redis database number (default 0).
    """

    host: str = "localhost"
    port: int = 6379
    db: int = 0


@dataclass(frozen=True)
class ValkeyConfig:
    """Connection settings for the Valkey backend.

    Attributes:
        addresses (list[tuple[str, int]]): (host, port) pairs for the Valkey nodes.
            Defaults to a single localhost:6379 node.
    """

    addresses: list[tuple[str, int]] = field(default_factory=lambda: [("localhost", 6379)])


def validate_queue_maxsize(value: int) -> int:
    """Return ``value`` if it is a positive int, else raise ValueError."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"queue_maxsize must be a positive int, got {value!r}")
    return value
```

**Design rationale.** `LoggingConfig` is the single typed seam for the shared machinery. `backend_config` is typed as `Any` at the base level to avoid `config.py` importing the backend modules (which would create a cycle); concrete handlers narrow it to `RedisConfig`/`ValkeyConfig` via their own typed constructor params. `RedisConfig`/`ValkeyConfig` replace the raw `dict`/`GlideClientConfiguration` magic-key configs with validated, typed objects.

### How each handler consumes it (backward-compatible)

Each handler **keeps its current public keyword signature** and internally builds a `LoggingConfig` (plus its backend config) from those kwargs, then validates. The `**kwargs` catch-all is removed from `AsyncLoggingHandler` and replaced with an explicit rejection of unknown kwargs, so a typo raises `TypeError` at construction instead of being silently swallowed.

- `AsyncLoggingHandler.__init__` drops `**kwargs`. It builds `self.config = LoggingConfig(service_name=..., worker_id=..., error_handler=..., queue_maxsize=validate_queue_maxsize(queue_maxsize))` and reads `self.queue_maxsize = self.config.queue_maxsize`, `self.error_handler = self.config.error_handler`. Because it no longer accepts `**kwargs`, any unknown kwarg now raises `TypeError` from Python itself — the loud failure AR-008 wants.
- `AsyncBaseHandler.__init__` drops `**kwargs`, adds `stdout_enable` to the `LoggingConfig` it builds, and stores `self.config`. It keeps `self.stdout_enable` as a convenience alias for backward compat (tests read `handler.stdout_enable` at `tests/test_basic_handler.py:42,89,200`).
- `AsyncBrokerHandler.__init__` keeps `queue_name` positional, drops `**kwargs`, and forwards only the explicit shared kwargs to super. It stores `self.queue_name`.
- `AsyncRedisHandler.__init__` keeps `stream_name` and `redis_config` (a `dict` for backward compat), converts `redis_config` into a `RedisConfig`, and stores `self.config.backend_config = RedisConfig(...)`. It keeps `self.client_config` as a `dict` for backward compat (used by `connect()` at `redis_handler.py:83`).
- `AsyncValkeyHandler.__init__` keeps `stream_name` and `valkey_config` (a `GlideClientConfiguration` for backward compat), stores it as `self.config.backend_config`, and keeps `self.client_config` for `connect()`.

**Important subtlety:** because `AsyncLoggingHandler` no longer accepts `**kwargs`, the intermediate classes must forward **only the explicit shared kwargs** (`service_name`, `worker_id`, `error_handler`, `queue_maxsize`, `stdout_enable`) up the chain — never a `**kwargs` blob. This is what makes unknown kwargs fail loudly at the concrete handler's own `__init__` (Python raises `TypeError: unexpected keyword argument`).

---

## Ordered Migration Steps

Each task is independently verifiable. Tasks 1-3 build the config module and thread it through the pure-machinery and console layers; Task 4 threads it through the broker base; Tasks 5-6 through the concrete backends; Task 7 adds the loud-failure guarantee tests; Task 8 updates docs/examples.

### Task 1: Add the typed config module

**Files:**
- Create: `src/scietex/logging/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `LoggingConfig`, `RedisConfig`, `ValkeyConfig` frozen dataclasses and `validate_queue_maxsize(value: int) -> int` as specified in the Target Design above.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for the typed configuration objects (AR-008)."""

import pytest

from scietex.logging.config import (
    LoggingConfig,
    RedisConfig,
    ValkeyConfig,
    validate_queue_maxsize,
)


def test_logging_config_defaults():
    cfg = LoggingConfig()
    assert cfg.service_name == "Service"
    assert cfg.worker_id == 1
    assert cfg.error_handler is None
    assert cfg.queue_maxsize == 10000
    assert cfg.stdout_enable is True
    assert cfg.backend_config is None


def test_logging_config_is_frozen():
    cfg = LoggingConfig()
    with pytest.raises(Exception):
        cfg.queue_maxsize = 5  # frozen dataclass rejects attribute assignment


def test_redis_config_defaults():
    cfg = RedisConfig()
    assert cfg.host == "localhost"
    assert cfg.port == 6379
    assert cfg.db == 0


def test_valkey_config_defaults():
    cfg = ValkeyConfig()
    assert cfg.addresses == [("localhost", 6379)]


def test_validate_queue_maxsize_accepts_positive_int():
    assert validate_queue_maxsize(5000) == 5000


@pytest.mark.parametrize("bad", [0, -1, 1.5, "10000", True, None])
def test_validate_queue_maxsize_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        validate_queue_maxsize(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scietex.logging.config'`.

- [ ] **Step 3: Write the config module**

Create `src/scietex/logging/config.py` exactly as in the Target Design section above.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/config.py tests/test_config.py
git commit -m "feat: add typed config objects for handlers (AR-008)"
```

---

### Task 2: Thread `LoggingConfig` through `AsyncLoggingHandler` and drop `**kwargs`

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:99-141`
- Test: `tests/test_async_logging_handler.py`

**Interfaces:**
- Consumes: `LoggingConfig`, `validate_queue_maxsize` from `config.py`.
- Produces: `AsyncLoggingHandler.__init__(service_name=None, worker_id=None, *, error_handler=None, queue_maxsize=10000)` — **no `**kwargs`**. New attribute `self.config: LoggingConfig`. `self.queue_maxsize` and `self.error_handler` remain as aliases read from `self.config`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_async_logging_handler.py`:

```python
def test_unknown_kwarg_raises_type_error():
    """A typo'd kwarg fails loudly instead of being silently swallowed."""
    with pytest.raises(TypeError):
        BareHandler(service_name="TestService", worker_id=1, stdout_enabel=True)


def test_config_exposes_machinery_options():
    handler = BareHandler(
        service_name="Svc", worker_id=7, queue_maxsize=123, error_handler=lambda r, e: None
    )
    assert handler.config.service_name == "Svc"
    assert handler.config.worker_id == 7
    assert handler.config.queue_maxsize == 123
    assert handler.config.error_handler is not None
    assert handler.queue_maxsize == 123
    assert handler.error_handler is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_unknown_kwarg_raises_type_error tests/test_async_logging_handler.py::test_config_exposes_machinery_options -q`
Expected: FAIL — `BareHandler` still accepts `**kwargs` (no `TypeError`), and `handler.config` does not exist (`AttributeError`).

- [ ] **Step 3: Implement**

In `async_logging_handler.py`:
1. Add `from .config import LoggingConfig, validate_queue_maxsize` at the top (after the existing `from .formatter import ScietexFormatter` import).
2. Change the `__init__` signature to remove `**kwargs`:

```python
    def __init__(
        self,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
    ) -> None:
```

3. Replace the body's option handling (lines 125-132) with config construction:

```python
        super().__init__()
        if worker_id is None:
            worker_id = 1
        if service_name is None:
            service_name = "Service"
        self.config = LoggingConfig(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            queue_maxsize=validate_queue_maxsize(queue_maxsize),
        )
        self.error_handler = self.config.error_handler
        self.queue_maxsize = self.config.queue_maxsize
        self.formatter = ScietexFormatter(
            service_name=self.config.service_name, worker_id=self.config.worker_id
        )
```

4. Update the `__init__` docstring: remove the `**kwargs` paragraph (lines 122-123) and note that unknown kwargs now raise `TypeError`.

- [ ] **Step 4: Run the full non-live suite to verify nothing else broke**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py -q`
Expected: FAIL — `AsyncBaseHandler` still forwards `**kwargs` to `AsyncLoggingHandler`, which no longer accepts it, so `AsyncBaseHandler(...)` raises `TypeError`. This is expected; Task 3 fixes it. (If you want a green checkpoint, run only `tests/test_async_logging_handler.py`.)

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_async_logging_handler.py
git commit -m "feat: thread LoggingConfig through AsyncLoggingHandler, drop **kwargs (AR-008)"
```

---

### Task 3: Thread `LoggingConfig` through `AsyncBaseHandler` and drop `**kwargs`

**Files:**
- Modify: `src/scietex/logging/basic_handler.py:31-81`
- Test: `tests/test_basic_handler.py`

**Interfaces:**
- Consumes: `LoggingConfig` from `config.py`; `AsyncLoggingHandler` (now without `**kwargs`).
- Produces: `AsyncBaseHandler.__init__(service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000)` — **no `**kwargs`**. `self.config.stdout_enable` set; `self.stdout_enable` kept as an alias.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_basic_handler.py`:

```python
def test_unknown_kwarg_raises_type_error_on_base_handler():
    """A typo'd kwarg on AsyncBaseHandler fails loudly."""
    with pytest.raises(TypeError):
        AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enabel=True)


def test_config_exposes_stdout_enable():
    handler = AsyncBaseHandler(service_name="TestService", worker_id=1, stdout_enable=False)
    assert handler.config.stdout_enable is False
    assert handler.stdout_enable is False  # backward-compat alias
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_basic_handler.py::test_unknown_kwarg_raises_type_error_on_base_handler tests/test_basic_handler.py::test_config_exposes_stdout_enable -q`
Expected: FAIL — `AsyncBaseHandler` still accepts `**kwargs` and `handler.config` does not exist.

- [ ] **Step 3: Implement**

In `basic_handler.py`:
1. Add `from .config import LoggingConfig` at the top.
2. Change the `__init__` signature to remove `**kwargs`:

```python
    def __init__(
        self,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
    ) -> None:
```

3. Replace the `super().__init__(...)` call (lines 61-67) to forward only explicit kwargs and set `stdout_enable` on the config:

```python
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
        )
        self.config = LoggingConfig(
            service_name=self.config.service_name,
            worker_id=self.config.worker_id,
            error_handler=self.config.error_handler,
            queue_maxsize=self.config.queue_maxsize,
            stdout_enable=stdout_enable,
        )
        self.stdout_enable = self.config.stdout_enable
        self._console_backend: ConsoleBackend | None = None
        if self.stdout_enable:
            self._console_backend = ConsoleBackend(
                self.formatter,
                self.logging_running_event,
                maxsize=self.config.queue_maxsize,
            )
            self.register_backend(
                "console",
                self._console_backend.queue,
                self._console_backend._worker,
                self._console_backend.drain,
            )
```

4. Update the `__init__` docstring: remove the `**kwargs` paragraph (line 55) and note unknown kwargs raise `TypeError`.

- [ ] **Step 4: Run the full non-live suite to verify green**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py tests/test_console_backend.py -q`
Expected: PASS. (Broker tests are excluded here because `AsyncBrokerHandler` still forwards `**kwargs`; Task 4 fixes that.)

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/basic_handler.py tests/test_basic_handler.py
git commit -m "feat: thread LoggingConfig through AsyncBaseHandler, drop **kwargs (AR-008)"
```

---

### Task 4: Thread `LoggingConfig` through `AsyncBrokerHandler` and drop `**kwargs`

**Files:**
- Modify: `src/scietex/logging/message_broker_handler.py:38-63`
- Test: `tests/test_message_broker_handler.py`

**Interfaces:**
- Consumes: `AsyncBaseHandler` (now without `**kwargs`).
- Produces: `AsyncBrokerHandler.__init__(queue_name, service_name=None, worker_id=None, *, error_handler=None, stdout_enable=True, queue_maxsize=10000)` — **no `**kwargs`**. `queue_name` stays positional. `self.queue_name` kept.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_message_broker_handler.py`:

```python
def test_broker_unknown_kwarg_raises_type_error():
    """A typo'd kwarg on a broker handler fails loudly."""
    with pytest.raises(TypeError):
        FakeBrokerHandler(queue_name="broker", stdout_enabel=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_message_broker_handler.py::test_broker_unknown_kwarg_raises_type_error -q`
Expected: FAIL — `FakeBrokerHandler` still forwards `**kwargs` and the typo is silently swallowed.

- [ ] **Step 3: Implement**

In `message_broker_handler.py`, change the `__init__` signature (lines 38-44) and the `super().__init__` call (line 58):

```python
    def __init__(
        self,
        queue_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
    ) -> None:
```

```python
        super().__init__(
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
        )
        self.queue_name: str = queue_name
        self.client: Any | None = None
        self.register_backend(
            self.queue_name,
            asyncio.Queue(maxsize=self.queue_maxsize),
            self._worker,
            self.drain,
        )
```

Update the docstring: remove the `**kwargs` paragraph (line 52) and note unknown kwargs raise `TypeError`. Add `from collections.abc import Callable` and `import logging` imports if not already present (check the top of the file — it currently imports `abc`, `asyncio`, `datetime`, `typing.Any`; add `logging` and `Callable`).

- [ ] **Step 4: Run the full non-live suite to verify green**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_message_broker_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py tests/test_console_backend.py -q`
Expected: PASS. (Redis/Valkey tests are excluded here because those handlers still forward `**kwargs`; Tasks 5-6 fix that.)

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/message_broker_handler.py tests/test_message_broker_handler.py
git commit -m "feat: thread LoggingConfig through AsyncBrokerHandler, drop **kwargs (AR-008)"
```

---

### Task 5: Typed `RedisConfig` for `AsyncRedisHandler`

**Files:**
- Modify: `src/scietex/logging/redis_handler.py:35-69`
- Test: `tests/test_redis_handler.py` (add a unit test that does not require a live Redis)

**Interfaces:**
- Consumes: `RedisConfig` from `config.py`; `AsyncBrokerHandler` (now without `**kwargs`).
- Produces: `AsyncRedisHandler.__init__(stream_name, service_name=None, worker_id=None, *, redis_config=None, error_handler=None, stdout_enable=True, queue_maxsize=10000)` — **no `**kwargs`**. `redis_config` stays a `dict` for backward compat but is validated into a `RedisConfig` stored as `self.config.backend_config`. `self.client_config` stays a `dict` for `connect()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_redis_handler.py` (this test does not touch a live server):

```python
def test_redis_config_is_typed_and_validated():
    """redis_config dict is converted into a typed RedisConfig on the handler."""
    handler = AsyncRedisHandler(
        stream_name="s",
        redis_config={"host": "example.com", "port": 7000, "db": 2},
    )
    assert handler.config.backend_config.host == "example.com"
    assert handler.config.backend_config.port == 7000
    assert handler.config.backend_config.db == 2
    # client_config remains a dict for the redis client call.
    assert handler.client_config == {"host": "example.com", "port": 7000, "db": 2}


def test_redis_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError):
        AsyncRedisHandler(stream_name="s", stdout_enabel=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_redis_handler.py::test_redis_config_is_typed_and_validated tests/test_redis_handler.py::test_redis_unknown_kwarg_raises_type_error -q`
Expected: FAIL — `handler.config` does not exist and `**kwargs` is still accepted.

- [ ] **Step 3: Implement**

In `redis_handler.py`:
1. Add `from .config import RedisConfig` at the top.
2. Change the `__init__` signature (lines 35-42) to remove `**kwargs` and make the shared options explicit keyword-only:

```python
    def __init__(
        self,
        stream_name: str,
        service_name: str | None = None,
        worker_id: int | None = None,
        *,
        redis_config: dict | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        stdout_enable: bool = True,
        queue_maxsize: int = 10000,
    ) -> None:
```

3. Replace the `super().__init__` call (lines 58-63) and the `client_config` assignment (lines 64-69):

```python
        super().__init__(
            queue_name="redis",
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
        )
        self.stream_name = stream_name
        raw = redis_config or {"host": "localhost", "port": 6379, "db": 0}
        self.config = LoggingConfig(
            service_name=self.config.service_name,
            worker_id=self.config.worker_id,
            error_handler=self.config.error_handler,
            queue_maxsize=self.config.queue_maxsize,
            stdout_enable=self.config.stdout_enable,
            backend_config=RedisConfig(**raw),
        )
        self.client_config: dict = raw
```

Note: `RedisConfig(**raw)` will raise `TypeError` if `raw` contains an unknown key (e.g. a typo'd `"hst"`), which is the loud-failure behavior AR-008 wants for the magic dict keys. Add `from .config import LoggingConfig, RedisConfig` and the `logging`/`Callable` imports if not present.

4. Update the docstring: remove the `**kwargs` paragraph (line 52).

- [ ] **Step 4: Run the new unit tests plus the live test if a server is available**

Run: `uv run pytest tests/test_redis_handler.py -q`
Expected: PASS if a local Redis is running (the live test at `tests/test_redis_handler.py:15`); otherwise the two new unit tests pass and the live test errors on connection. If no Redis is available, run only the two new tests: `uv run pytest tests/test_redis_handler.py::test_redis_config_is_typed_and_validated tests/test_redis_handler.py::test_redis_unknown_kwarg_raises_type_error -q`.

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/redis_handler.py tests/test_redis_handler.py
git commit -m "feat: typed RedisConfig for AsyncRedisHandler, drop **kwargs (AR-008)"
```

---

### Task 6: Typed `ValkeyConfig` for `AsyncValkeyHandler`

**Files:**
- Modify: `src/scietex/logging/valkey_handler.py:35-69`
- Test: `tests/test_valkey_handler.py` (add a unit test that does not require a live Valkey)

**Interfaces:**
- Consumes: `ValkeyConfig` from `config.py`; `AsyncBrokerHandler` (now without `**kwargs`).
- Produces: `AsyncValkeyHandler.__init__(stream_name, service_name=None, worker_id=None, *, valkey_config=None, error_handler=None, stdout_enable=True, queue_maxsize=10000)` — **no `**kwargs`**. `valkey_config` stays a `GlideClientConfiguration` for backward compat; `self.config.backend_config` is a `ValkeyConfig`; `self.client_config` stays a `GlideClientConfiguration` for `connect()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_valkey_handler.py` (no live server needed):

```python
def test_valkey_config_is_typed():
    """valkey_config is reflected in a typed ValkeyConfig on the handler."""
    handler = AsyncValkeyHandler(stream_name="s")
    assert handler.config.backend_config.addresses == [("localhost", 6379)]
    assert handler.client_config is not None  # GlideClientConfiguration kept for connect()


def test_valkey_unknown_kwarg_raises_type_error():
    with pytest.raises(TypeError):
        AsyncValkeyHandler(stream_name="s", stdout_enabel=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_valkey_handler.py::test_valkey_config_is_typed tests/test_valkey_handler.py::test_valkey_unknown_kwarg_raises_type_error -q`
Expected: FAIL — `handler.config` does not exist and `**kwargs` is still accepted.

- [ ] **Step 3: Implement**

In `valkey_handler.py`:
1. Add `from .config import LoggingConfig, ValkeyConfig` at the top.
2. Change the `__init__` signature (lines 35-42) to remove `**kwargs` and make shared options explicit keyword-only (mirror Task 5's signature, with `valkey_config` in place of `redis_config`).
3. Replace the `super().__init__` call (lines 58-63) and the `client_config` assignment (lines 64-69):

```python
        super().__init__(
            queue_name="valkey",
            service_name=service_name,
            worker_id=worker_id,
            error_handler=error_handler,
            stdout_enable=stdout_enable,
            queue_maxsize=queue_maxsize,
        )
        self.stream_name = stream_name
        self.client_config: GlideClientConfiguration
        if valkey_config is not None:
            self.client_config = valkey_config
        else:
            self.client_config = GlideClientConfiguration([NodeAddress()])
        self.config = LoggingConfig(
            service_name=self.config.service_name,
            worker_id=self.config.worker_id,
            error_handler=self.config.error_handler,
            queue_maxsize=self.config.queue_maxsize,
            stdout_enable=self.config.stdout_enable,
            backend_config=ValkeyConfig(
                addresses=[
                    (node.host, node.port)
                    for node in self.client_config.addresses
                ]
            ),
        )
```

Note: `GlideClientConfiguration` exposes `.addresses` as a list of `NodeAddress` objects with `.host` and `.port` attributes. If the exact attribute names differ in the installed `valkey-glide` version, adjust the comprehension to match (verify against the installed package before finalizing this step). `self.config.backend_config` is informational/typed; `connect()` continues to use `self.client_config` unchanged.

4. Update the docstring: remove the `**kwargs` paragraph (line 52).

- [ ] **Step 4: Run the new unit tests plus the live test if a server is available**

Run: `uv run pytest tests/test_valkey_handler.py -q`
Expected: PASS if a local Valkey is running; otherwise the two new unit tests pass and the live test errors on connection. If no Valkey is available, run only the two new tests.

- [ ] **Step 5: Commit**

```bash
git add src/scietex/logging/valkey_handler.py tests/test_valkey_handler.py
git commit -m "feat: typed ValkeyConfig for AsyncValkeyHandler, drop **kwargs (AR-008)"
```

---

### Task 7: Full-suite verification and lint

**Files:**
- No source changes (verification only).

- [ ] **Step 1: Run the full non-live test suite**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_message_broker_handler.py tests/test_queue_bounds.py tests/test_console_backend.py tests/test_restartable_lifecycle.py tests/test_formatter.py tests/test_version.py tests/test_config.py -q`
Expected: PASS (all green). Redis/Valkey live tests are run separately only when a server is present.

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/scietex/logging/ tests/`
Expected: no errors. Fix any unused imports (e.g. `Any` in `message_broker_handler.py` if it becomes unused, or `Callable`/`logging` additions).

- [ ] **Step 3: Run type check if configured**

Run: `uv run ty check src/scietex/logging/` (if `ty` is available in the env; otherwise skip).
Expected: no new type errors.

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -u
git commit -m "chore: lint fixes after typed-config refactor (AR-008)"
```

---

### Task 8: Update docs and examples

**Files:**
- Modify: `docs/configuration.md`, `docs/backends.md`, `docs/architecture/components.md`, `docs/architecture/overview.md` (only the constructor-signature lines), `src/scietex/logging/__init__.py` (docstring only, if it mentions `**kwargs`).
- Test: none (docs only); verify by reading.

- [ ] **Step 1: Update `docs/configuration.md`**

Add a "Typed Configuration" subsection under "Handler Configuration" documenting `LoggingConfig`, `RedisConfig`, `ValkeyConfig`, and that unknown kwargs now raise `TypeError`. Update the `queue_maxsize` paragraph (lines 105-121) to note it is validated to a positive int.

- [ ] **Step 2: Update `docs/backends.md`**

Update the Redis/Valkey "Configuration" sections (lines 49-56, 81-84) to mention the typed `RedisConfig`/`ValkeyConfig` objects while noting the `redis_config` dict / `valkey_config` object constructor params remain accepted for backward compat.

- [ ] **Step 3: Update `docs/architecture/components.md`**

Update the constructor-signature lines for `AsyncLoggingHandler` (line 64), `AsyncBaseHandler` (line 131), `AsyncBrokerHandler` (line 157), `AsyncRedisHandler` (lines 192-194), `AsyncValkeyHandler` (lines 215-217) to drop `**kwargs` and note the `self.config` attribute. Add a short "Configuration" note pointing at `config.py`.

- [ ] **Step 4: Update `docs/architecture/overview.md`**

Update the "Public signature unchanged" note (line 42) to reflect that signatures are unchanged but `**kwargs` is gone and unknown kwargs now raise `TypeError`.

- [ ] **Step 5: Verify docs render / no stale `**kwargs` claims**

Run: `uv run ruff check src/scietex/logging/__init__.py` (docstring is not linted for content, but confirms no syntax break). Grep for stale `**kwargs` in the modified docs and confirm each remaining mention is intentional (e.g. the `AsyncLoggingHandler` subclass example in `docs/configuration.md:78` that shows a user subclass forwarding `**kwargs` — update that example to forward explicit kwargs).

- [ ] **Step 6: Commit**

```bash
git add docs/configuration.md docs/backends.md docs/architecture/components.md docs/architecture/overview.md src/scietex/logging/__init__.py
git commit -m "docs: document typed configuration and loud unknown-kwarg failure (AR-008)"
```

---

## Test Strategy

### Existing tests that break and how they are handled

No existing test asserts the silent-swallow behavior, so none *semantically* breaks. The only mechanical break is that intermediate classes that forwarded `**kwargs` must now forward explicit kwargs — this is handled inside Tasks 2-6, not in the tests. The existing tests construct handlers with only valid kwargs, so they keep passing once each layer is migrated. The full non-live suite is the regression gate at Tasks 3, 4, and 7.

### New tests added

- `tests/test_config.py` — defaults, frozen-ness, and `validate_queue_maxsize` accept/reject (Task 1).
- `tests/test_async_logging_handler.py` — unknown kwarg raises `TypeError`; `self.config` exposes machinery options (Task 2).
- `tests/test_basic_handler.py` — unknown kwarg raises `TypeError`; `self.config.stdout_enable` + backward-compat `self.stdout_enable` alias (Task 3).
- `tests/test_message_broker_handler.py` — unknown kwarg raises `TypeError` on a broker handler (Task 4).
- `tests/test_redis_handler.py` — `redis_config` dict → typed `RedisConfig`; unknown kwarg raises `TypeError` (Task 5, no live server needed).
- `tests/test_valkey_handler.py` — typed `ValkeyConfig`; unknown kwarg raises `TypeError` (Task 6, no live server needed).

### The core behavioral guarantee to test

The loud-failure guarantee: **any unknown/typo'd kwarg on any handler constructor raises `TypeError` at construction** rather than being silently swallowed at the top of the MRO chain. This is the direct fix for the AR-008 footgun and is asserted in every layer's new test.

---

## Risks / Open Questions (need explicit user sign-off)

1. **Backward-compat of the public constructor API.** The recommended design keeps every existing keyword signature working (all examples/docs/tests unchanged). The alternative — a full config-object-first API where callers pass `AsyncBaseHandler(config=LoggingConfig(...))` — is a **breaking** change to every documented call site and is not recommended for a MEDIUM finding. **Decision needed:** confirm backward-compat is required (recommended) vs. a clean break is acceptable.

2. **How far to go: full config object vs. validated kwargs.** The recommended design is "validated kwargs that build a typed `LoggingConfig` internally" — it delivers typed, validated config and loud failure without breaking callers. The more aggressive option (constructors take a `config:` object as the primary argument) is cleaner long-term but breaks the public API and is disproportionate for MEDIUM. **Decision needed:** confirm the "typed config built from validated kwargs, API kept" scope.

3. **Per-backend config granularity (deferred from AR-007).** AR-007's resolution note says `queue_maxsize` flows through `**kwargs` to Redis/Valkey unchanged. This plan folds `queue_maxsize` (and `stdout_enable`, `error_handler`) into the shared `LoggingConfig` and gives each backend a typed `RedisConfig`/`ValkeyConfig`. It does **not** introduce a per-backend overflow/retry/error-policy config (that is AR-007's cross-cutting concern, already resolved as drop+report). **Decision needed:** confirm per-backend *connection* config granularity is sufficient and no per-backend *policy* config is added in this pass.

4. **`queue_name` magic strings remain.** The `"console"`/`"redis"`/`"valkey"`/user `queue_name` registration keys are left as-is. Replacing them with a first-class backend-registration mechanism is AR-001's deferred recommendation and is out of AR-008 scope. **Decision needed:** confirm leaving `queue_name` magic strings in place for now is acceptable.

5. **`ValkeyConfig` field extraction.** Task 6 reads `node.host`/`node.port` off `GlideClientConfiguration.addresses`. The exact attribute names depend on the installed `valkey-glide` version (2.5.0 per `pyproject.toml:22`). The implementing agent must verify against the installed package; if the shape differs, the `ValkeyConfig` construction must adapt. This is a low-risk implementation detail, flagged for awareness.

---

## Final Verification Command Set

```bash
# Full non-live suite (Redis/Valkey live tests excluded)
uv run pytest tests/test_async_logging_handler.py tests/test_basic_handler.py \
  tests/test_message_broker_handler.py tests/test_queue_bounds.py \
  tests/test_console_backend.py tests/test_restartable_lifecycle.py \
  tests/test_formatter.py tests/test_version.py tests/test_config.py -q

# Lint
uv run ruff check src/scietex/logging/ tests/

# Type check (if ty is available)
uv run ty check src/scietex/logging/

# Live backend tests (only when a local Redis/Valkey is running)
uv run pytest tests/test_redis_handler.py tests/test_valkey_handler.py -q

# Examples still run (console example needs no server)
uv run python examples/basic_console_logging.py
```

Expected: all non-live tests pass; ruff clean; `ty` clean; live tests pass when servers are present; the console example runs without error.

---

## Handoff Plan

1. **Task 1** — Create `src/scietex/logging/config.py` (frozen `LoggingConfig`, `RedisConfig`, `ValkeyConfig`, `validate_queue_maxsize`) and `tests/test_config.py`. Verify: `uv run pytest tests/test_config.py -q`.
2. **Task 2** — In `async_logging_handler.py:99-141`, drop `**kwargs`, build `self.config = LoggingConfig(...)`, keep `self.queue_maxsize`/`self.error_handler` aliases. Verify: `uv run pytest tests/test_async_logging_handler.py -q`.
3. **Task 3** — In `basic_handler.py:31-81`, drop `**kwargs`, forward explicit kwargs to super, set `self.config.stdout_enable`, keep `self.stdout_enable` alias. Verify: `uv run pytest tests/test_basic_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py -q`.
4. **Task 4** — In `message_broker_handler.py:38-63`, drop `**kwargs`, make shared options explicit keyword-only, keep `queue_name` positional. Verify: `uv run pytest tests/test_message_broker_handler.py -q`.
5. **Task 5** — In `redis_handler.py:35-69`, drop `**kwargs`, convert `redis_config` dict → `RedisConfig` stored as `self.config.backend_config`, keep `self.client_config` dict. Verify: `uv run pytest tests/test_redis_handler.py::test_redis_config_is_typed_and_validated tests/test_redis_handler.py::test_redis_unknown_kwarg_raises_type_error -q`.
6. **Task 6** — In `valkey_handler.py:35-69`, drop `**kwargs`, store `ValkeyConfig` as `self.config.backend_config`, keep `self.client_config` GlideClientConfiguration. **Verify the `NodeAddress` attribute names against the installed `valkey-glide` before finalizing the addresses comprehension.** Verify: `uv run pytest tests/test_valkey_handler.py::test_valkey_config_is_typed tests/test_valkey_handler.py::test_valkey_unknown_kwarg_raises_type_error -q`.
7. **Task 7** — Full non-live suite + `ruff check` + `ty check`. Verify: all green.
8. **Task 8** — Update `docs/configuration.md`, `docs/backends.md`, `docs/architecture/components.md`, `docs/architecture/overview.md`, and the `AsyncLoggingHandler` subclass example in `docs/configuration.md:78` (it currently forwards `**kwargs`). Verify: grep the docs for stale `**kwargs` claims.

- **Risk:** The intermediate classes must forward **only explicit shared kwargs** up the chain — never a `**kwargs` blob — or the loud-failure guarantee silently regresses. Watch for any leftover `**kwargs` in the four handler `__init__` signatures.
- **Risk:** `ValkeyConfig` field extraction depends on `valkey-glide`'s `NodeAddress` attribute names; verify against the installed version.
- **Risk:** Do not import any handler module from `config.py` (import cycle). `config.py` must import only stdlib.
- **Test:** The loud-failure guarantee is the acceptance test — a typo'd kwarg (`stdout_enabel=True`) must raise `TypeError` on every handler class. Run the full non-live suite plus `ruff check` and `ty check` before claiming completion.
