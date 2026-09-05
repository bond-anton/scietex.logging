# P2 Architecture Refactors (AR-016/017/023 config, AR-018 broker decoupling, AR-019 drain contract) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the three Priority-2 structural findings — the write-only typed-config subsystem (AR-016/017/023, one coherent decision), the broker wire-format coupling to `ScietexFormatter` internals (AR-018), and the fragile shared-mutable drain contract (AR-019).

**Architecture:** Three independent workstreams. (1) **Config refactor** — make `LoggingConfig` the single runtime source of truth: handlers read `self.config.*` at work time instead of parallel flat state; `backend_config` becomes a real union; `RedisConfig` mirrors the full client option surface; backend config is threaded through `__init__` instead of reconstructing `LoggingConfig` post-hoc. (2) **AR-018** — derive broker `name`/`time` from `self.config` (identity) + the record (time), not from the formatter; the formatter only renders. (3) **AR-019** — `stop_logging` owns result collection; drain hooks *return* their `BackendDrainResult`; the console status report becomes an explicit post-drain observer.

**Tech Stack:** Python >=3.10, asyncio, stdlib `dataclasses`/`typing`, pytest-asyncio, ruff, ty. No new dependencies.

**Spec:** `docs/reviews/architecture/2026-09-05.md` findings AR-016 (lines 222-242), AR-017 (244-260), AR-018 (262-284), AR-019 (288-301), AR-023 (343-358), plus the cross-cutting guidance (lines 550-563) and the P2 recommendation (lines 583-593). Executors read both this plan and the review.

## Global Constraints

- Working tree must stay green after each task: `uv run pytest -q`, `uv run ruff check .`, `uv run ty check src/scietex/logging/`.
- Do NOT modify files outside the exact paths listed per task. AR-020 (queue-clear semantics), AR-021 (console error channel), AR-022 (backoff), AR-024 (formatter injection), AR-025 (config-input asymmetry), AR-026 (`level_abbreviation` relocation), AR-027..AR-036 are **out of scope** — do not implement them here. AR-018 must be fixed **before** any AR-024 formatter-injection work (the review's cross-cutting note, lines 556-559); AR-024 is P3 and not in this plan.
- Preserve the strict one-way acyclic import graph. `config.py` and `formatter.py` import only stdlib. `config.py` must never import a handler module (cycle). `message_broker_handler.py` currently imports `formatter` only for `level_abbreviation` (`message_broker_handler.py:12`); after AR-018 it must no longer read `self.formatter` for `name`/`time`, but the `level_abbreviation` import may remain (relocating it is AR-026, out of scope).
- **Public constructor API stays backward-compatible.** Every existing call site in `examples/`, `docs/`, `tests/`, and `__init__.py` docstrings constructs handlers with keyword args like `AsyncRedisHandler(stream_name=..., redis_config={...})`, `AsyncValkeyHandler(stream_name=..., valkey_config=...)`. These must keep working unchanged. `self.config`, `self.queue_maxsize`, `self.stdout_enable`, `self.error_handler`, `self.client_config`, `self.formatter` remain readable attributes (tests read them).
- No emoji in code/comments. Comments explain WHY, not WHAT. No commented-out code. No `@ts-ignore`-style suppressions.
- Every public function/method needs at least one caller before commit; no dead code.
- Commit style follows repo history: `refactor: ...`, `fix: ...`, `test: ...`, `docs: ...` conventional prefixes (see `git log --oneline`).
- The working tree currently has 3 uncommitted doc edits (`docs/configuration.md`, two plan files). Do not stage or commit those unless the task explicitly touches them; stage only the files each task lists.

---

## Config Decision (AR-016/017/023) — read this first

The review (lines 550-553) states the decision is **binary**: make `LoggingConfig` the single runtime source of truth, **or** drop the typed layer and keep the flat kwargs. Keeping both guarantees drift. The three findings must be planned together.

### Option A — Make `LoggingConfig` the single runtime source of truth (RECOMMENDED)

Handlers read `self.config.*` at work time; the parallel flat state (`self.queue_maxsize`, `self.stdout_enable`, `self.error_handler`, `self.client_config`) is removed or demoted to thin read-only aliases over `self.config`. `backend_config` becomes a real union `RedisConfig | ValkeyConfig | None`. `RedisConfig` mirrors the full redis client option surface. Backend config is threaded through `__init__` (each subclass passes its backend config up to a base that assembles one `LoggingConfig`), eliminating the post-hoc `LoggingConfig(...)` reconstruction in `redis_handler.py:83-90` and `valkey_handler.py:85-94`.

**Why recommended:** It delivers the review's target architecture (lines 635-637: "Config is a single source of truth: `LoggingConfig` ... is read at work time, not shadowed by parallel flat state"). It also gives AR-018 a natural home for identity (`service_name`/`worker_id` already live on `LoggingConfig`), so the two workstreams compose. It preserves the typed-config investment from AR-008 (which the repo deliberately built) rather than throwing it away. Cost: a focused refactor of the four handler `__init__` bodies and the `connect()`/`_worker()` read sites.

### Option B — Drop the typed layer, keep flat kwargs

Delete `LoggingConfig`/`RedisConfig`/`ValkeyConfig` (or stop building `self.config`), keep `self.queue_maxsize`/`self.stdout_enable`/`self.error_handler`/`self.client_config` as the sole state, and delete `backend_config`. `config.py` keeps only `validate_queue_maxsize` and `optional_dependency_error`.

**Why not recommended:** It discards the AR-008 typed-config work and the loud-unknown-kwarg guarantee that depends on building `LoggingConfig` (a typo'd kwarg currently raises `TypeError` precisely because the constructors build a frozen `LoggingConfig` from explicit kwargs — see `test_unknown_kwarg_raises_type_error`). It also removes the natural carrier for AR-018's identity, forcing identity to be re-added as new flat state. It is a larger deletion with no runtime benefit over Option A.

**Decision needed (sign-off):** Option A is recommended. Confirm before Task 1. If Option B is chosen, this plan's config tasks must be rewritten (the AR-018 and AR-019 tasks are unaffected).

### Scope guardrail for the config refactor

AR-025 (unify the Redis-dict vs Valkey-`GlideClientConfiguration` input seam) is **out of scope** here. This plan keeps `redis_config: dict` and `valkey_config: GlideClientConfiguration` as the public constructor inputs (backward-compatible) and only fixes how they are *stored and read*. The Redis dict is passed through to `redis.Redis(**...)` unchanged (fixing AR-017 by *not* round-tripping it through a lossy `RedisConfig(**raw)`); the Valkey `GlideClientConfiguration` remains the client input. `RedisConfig`/`ValkeyConfig` become faithful *readable projections* of the client config for introspection, not the thing passed to the client.

---

## File Structure

Files touched across the three workstreams:

| File | Responsibility | Change |
|---|---|---|
| `src/scietex/logging/config.py` | Typed config leaf | AR-016/017/023: real union on `backend_config`; `RedisConfig` mirrors full client option surface; `ValkeyConfig` unchanged shape |
| `src/scietex/logging/async_logging_handler.py` | Machinery base + shutdown | AR-016: read `self.config.*` at work time; AR-019: coordinator owns result collection, hooks return results |
| `src/scietex/logging/basic_handler.py` | Console handler | AR-016: read `self.config.stdout_enable`; AR-019: register console as explicit post-drain observer |
| `src/scietex/logging/message_broker_handler.py` | Broker base + worker + drain | AR-016: read `self.config.*`; AR-018: derive `name`/`time` from config+record; AR-019: `drain` returns result |
| `src/scietex/logging/redis_handler.py` | Redis adapter | AR-016/017/023: thread backend config through `__init__`, drop post-hoc `LoggingConfig` rebuild, stop `RedisConfig(**raw)` |
| `src/scietex/logging/valkey_handler.py` | Valkey adapter | AR-016/023: thread backend config through `__init__`, drop post-hoc rebuild |
| `src/scietex/logging/console_backend.py` | Console sink | AR-019: `drain` returns result; add explicit `report_status(results)` observer |
| `tests/test_config.py` | Config unit tests | Update for union type + RedisConfig option surface |
| `tests/test_async_logging_handler.py` | Machinery tests | AR-019: drain-hook-returns-result tests |
| `tests/test_message_broker_handler.py` | Broker worker tests | AR-018: name/time derived from config not formatter |
| `tests/test_console_backend.py` | Console tests | AR-019: observer-based status reporting |
| `tests/test_redis_handler.py` | Redis tests | AR-017: valid extra options accepted; AR-016: config read at work time |
| `tests/test_valkey_handler.py` | Valkey tests | AR-016/023: config read at work time |
| `docs/reviews/architecture/2026-09-05.md` | Review record | Append Resolution notes for AR-016/017/018/019/023 |
| `docs/configuration.md` | Config doc | Update typed-config section for the single-source decision |
| `docs/backends.md` | Backend doc | Update Redis/Valkey config sections |
| `docs/architecture/components.md` | Component doc | Update constructor/state descriptions |
| `docs/architecture/data-flow.md` | Data-flow doc | Update Flow 2 (broker fields) and Flow 4 (drain) |
| `docs/architecture/lifecycle.md` | Lifecycle doc | Update construction + shutdown steps |
| `docs/architecture/overview.md` | Overview doc | Update config/drain descriptions |
| `docs/architecture/hotspots.md` | Hotspots doc | Update hotspots 2, 6, 11, 12 |
| `docs/advanced.md` | Custom-backend doc | Update drain-hook signature in the custom-backend example |

---

## Workstream 1: Config single-source refactor (AR-016/017/023)

### Task 1: `RedisConfig` mirrors the full client option surface; `backend_config` becomes a real union

**Files:**
- Modify: `src/scietex/logging/config.py:21-45`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `LoggingConfig`, `RedisConfig`, `ValkeyConfig` frozen dataclasses.
- Produces: `LoggingConfig.backend_config: RedisConfig | ValkeyConfig | None` (real union, not `Any`); `RedisConfig` gains the full set of redis client options as optional fields so `RedisConfig(**raw)` no longer raises on legitimate options. `ValkeyConfig` shape unchanged.

**Background (why):** `RedisConfig` declares only `host/port/db` (`config.py:43-45`), but `raw` is passed straight to `redis.Redis(**self.client_config)` (`redis_handler.py:105`), which accepts ~50 more options (`password`, `ssl`, `username`, `socket_timeout`, ... — verified against installed redis 8.1.0). `backend_config=RedisConfig(**raw)` (`redis_handler.py:89`) therefore raises `TypeError` on any dict containing a legitimate Redis option (AR-017). Separately, `backend_config: Any | None` (`config.py:30`) erases type checking at exactly the discriminated-union point (AR-023).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_redis_config_accepts_full_client_option_surface():
    """RedisConfig mirrors the redis client options, so RedisConfig(**raw) never rejects a valid option."""
    cfg = RedisConfig(
        host="example.com",
        port=7000,
        db=2,
        password="secret",
        username="svc",
        ssl=True,
        socket_timeout=5.0,
        health_check_interval=30,
    )
    assert cfg.host == "example.com"
    assert cfg.password == "secret"
    assert cfg.username == "svc"
    assert cfg.ssl is True


def test_backend_config_is_a_union_not_any():
    """LoggingConfig.backend_config is typed as a union of the backend configs, not Any."""
    import typing

    hints = typing.get_type_hints(LoggingConfig)
    assert hints["backend_config"] == RedisConfig | ValkeyConfig | None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_redis_config_accepts_full_client_option_surface tests/test_config.py::test_backend_config_is_a_union_not_any -v`
Expected: FAIL — `RedisConfig(...)` raises `TypeError: unexpected keyword argument 'password'`, and the type hint is `Any`.

- [ ] **Step 3: Implement**

In `src/scietex/logging/config.py`:

1. Change the `backend_config` field type and docstring (`config.py:21-30`):

```python
    backend_config: RedisConfig | ValkeyConfig | None = None
```

(Remove the now-unused `from typing import Any` import if nothing else uses it — check the file; `Any` is only used at `config.py:30`.)

2. Expand `RedisConfig` (`config.py:33-45`) to mirror the redis client's option surface. Keep `host`/`port`/`db` first (defaults unchanged) and add the commonly-used optional connection options as `None`-defaulted fields. The full redis 8.x surface is large; cover the options a user is most likely to pass (password, username, ssl family, timeouts, encoding, retry, health_check, client_name, protocol). Do NOT add `decode_responses` (the handler forces it True at `redis_handler.py:105`) or `connection_pool`/`redis_connect_func`/`event_dispatcher`/`credential_provider`/`maint_notifications_config` (objects, not plain config). The exact field set must be a superset of what `redis.Redis(**raw)` accepts for the options users realistically pass; if a user passes an option not yet declared, `RedisConfig(**raw)` would still raise — so prefer a generous superset. Verify the field names against the installed redis signature (see the grounding note in the review, AR-017 lines 244-260).

```python
@dataclass(frozen=True)
class RedisConfig:
    """Connection settings for the Redis backend.

    Mirrors the option surface of ``redis.Redis`` so that ``RedisConfig(**raw)``
    never rejects a legitimate client option. ``host``/``port``/``db`` are the
    common connection trio; the remaining fields are optional client options
    passed through to ``redis.Redis`` unchanged.
    """

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None
    socket_timeout: float | None = None
    socket_connect_timeout: float | None = None
    socket_keepalive: bool | None = None
    socket_keepalive_options: dict | None = None
    unix_socket_path: str | None = None
    encoding: str = "utf-8"
    encoding_errors: str = "strict"
    decode_responses: bool = False
    retry_on_timeout: bool = False
    retry_on_error: list | None = None
    ssl: bool = False
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None
    ssl_cert_reqs: str | None = None
    ssl_ca_certs: str | None = None
    ssl_ca_data: str | None = None
    ssl_check_hostname: bool = False
    ssl_min_version: str | None = None
    ssl_ciphers: str | None = None
    max_connections: int | None = None
    health_check_interval: int = 0
    client_name: str | None = None
    protocol: int | None = None
```

Note: `decode_responses` is declared here (default False) so `RedisConfig(**raw)` accepts a dict that includes it, but the handler's `connect()` still forces `decode_responses=True` at the client call (`redis_handler.py:105`) — the config field is a faithful projection, not the client input. If the installed redis version's signature differs from the above, adjust the field set to be a superset of the options the handler's `connect()` forwards.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the two new ones).

- [ ] **Step 5: Run the full non-live suite to check for regressions**

Run: `uv run pytest tests/test_config.py tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_message_broker_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py tests/test_console_backend.py tests/test_formatter.py tests/test_version.py -q`
Expected: PASS. (Redis/Valkey live tests excluded; run separately only when a server is present.)

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/config.py tests/test_config.py
git commit -m "refactor: type backend_config as a union and mirror full redis option surface (AR-017, AR-023)"
```

---

### Task 2: Thread backend config through `__init__`; make `LoggingConfig` the single source of truth

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:100-151`
- Modify: `src/scietex/logging/basic_handler.py:32-89`
- Modify: `src/scietex/logging/message_broker_handler.py:40-82`
- Modify: `src/scietex/logging/redis_handler.py:37-91`
- Modify: `src/scietex/logging/valkey_handler.py:37-94`
- Test: `tests/test_async_logging_handler.py`, `tests/test_redis_handler.py`, `tests/test_valkey_handler.py`

**Interfaces:**
- Consumes: `LoggingConfig`, `RedisConfig`, `ValkeyConfig` from Task 1.
- Produces: Each handler builds **one** `LoggingConfig` and reads `self.config.*` at work time. The post-hoc `LoggingConfig(...)` reconstruction in `redis_handler.py:83-90` and `valkey_handler.py:85-94` is eliminated. `self.queue_maxsize`, `self.stdout_enable`, `self.error_handler` become thin read-only aliases over `self.config` (kept for backward compat — tests read them). `self.client_config` remains the raw client input (dict for Redis, `GlideClientConfiguration` for Valkey) used by `connect()`.

**Background (why):** `self.config` is read only during construction (`async_logging_handler.py:138-141`, `basic_handler.py:69-82`, `redis_handler.py:83-90`, `valkey_handler.py:85-92`), never again; real behavior is driven by the parallel flat state `self.queue_maxsize`, `self.stdout_enable`, `self.error_handler`, `self.client_config` (AR-016). The `frozen=True` `LoggingConfig` forces each subclass to reconstruct the whole config post-hoc just to attach `backend_config` (AR-023). The fix: assemble one `LoggingConfig` per handler and read `self.config.*` at work time.

**Design:** The cleanest way to thread backend config through `__init__` without breaking the public signatures is to have each concrete handler build its own `LoggingConfig` (with `backend_config`) and pass it up, OR to add a protected `_build_config()` hook. Given the current structure (each `__init__` calls `super().__init__(...)` with explicit kwargs and then rebuilds `self.config`), the minimal change is:

- `AsyncLoggingHandler.__init__` builds `self.config = LoggingConfig(service_name, worker_id, error_handler, queue_maxsize)` and sets `self.error_handler = self.config.error_handler`, `self.queue_maxsize = self.config.queue_maxsize` (aliases). It does NOT set `stdout_enable` or `backend_config` (defaults apply).
- `AsyncBaseHandler.__init__` calls super, then rebuilds `self.config` **once** to add `stdout_enable` (as today, `basic_handler.py:69-75`), and reads `self.stdout_enable = self.config.stdout_enable`. It registers the console backend only when `self.config.stdout_enable` is True.
- `AsyncBrokerHandler.__init__` calls super (which now includes `stdout_enable`), then registers the broker queue/worker/drain. It reads `self.config.queue_maxsize` for the queue bound.
- `AsyncRedisHandler.__init__` calls super, then rebuilds `self.config` **once** to add `backend_config=RedisConfig(**raw)` — but now `RedisConfig(**raw)` no longer raises on valid options (Task 1). It keeps `self.client_config = raw` for `connect()`.
- `AsyncValkeyHandler.__init__` calls super, then rebuilds `self.config` **once** to add `backend_config=ValkeyConfig(addresses=...)`. It keeps `self.client_config` for `connect()`.

The post-hoc rebuild is retained (it is the mechanism AR-008 established and it is backward-compatible), but it now happens **once per handler** and the resulting `self.config` is the single source of truth read at work time. The "reconstruct whole config post-hoc" smell (AR-023) is reduced because the rebuild is a single, local `LoggingConfig(...)` that copies the already-validated shared fields — acceptable given the frozen dataclass. If the executor prefers, a `dataclasses.replace(self.config, backend_config=...)` is cleaner than a full rebuild; use `dataclasses.replace` where it reads better.

- [ ] **Step 1: Write the failing test (config is read at work time, not shadowed)**

The core behavioral guarantee: after construction, mutating the flat aliases must NOT change behavior, because behavior reads `self.config`. Concretely, assert that the handler's runtime state is driven by `self.config`:

Append to `tests/test_async_logging_handler.py`:

```python
def test_config_is_single_source_of_truth():
    """Runtime state reads self.config, not a parallel flat copy."""
    handler = BareHandler(service_name="Svc", worker_id=7, queue_maxsize=123)
    # The flat aliases mirror config; config is authoritative.
    assert handler.queue_maxsize == handler.config.queue_maxsize == 123
    assert handler.error_handler is handler.config.error_handler
    # The formatter identity comes from config.
    assert handler.formatter.worker_name == f"{handler.config.service_name}:{handler.config.worker_id}"
```

Append to `tests/test_redis_handler.py` (no live server needed):

```python
def test_redis_config_accepts_valid_extra_options():
    """A redis_config dict with legitimate client options no longer raises TypeError."""
    handler = AsyncRedisHandler(
        stream_name="s",
        redis_config={"host": "example.com", "port": 7000, "db": 2, "password": "secret", "ssl": True},
    )
    assert handler.config.backend_config.host == "example.com"
    assert handler.config.backend_config.password == "secret"
    assert handler.config.backend_config.ssl is True
    # client_config remains the raw dict passed to redis.Redis.
    assert handler.client_config == {
        "host": "example.com", "port": 7000, "db": 2, "password": "secret", "ssl": True,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_async_logging_handler.py::test_config_is_single_source_of_truth tests/test_redis_handler.py::test_redis_config_accepts_valid_extra_options -v`
Expected: FAIL — `test_redis_config_accepts_valid_extra_options` raises `TypeError` from `RedisConfig(**raw)` (Task 1 not yet applied to the handler, or the handler still uses the old narrow `RedisConfig`). `test_config_is_single_source_of_truth` may already pass if the aliases already mirror config — if so, that test is a guard, not the failing driver; the Redis test is the real failing driver.

- [ ] **Step 3: Implement the config threading**

In `src/scietex/logging/redis_handler.py`:

1. Update the `__init__` docstring (`redis_handler.py:55-57,70-71`) to remove the claim that unknown `redis_config` keys raise `TypeError` (they no longer do — the dict is passed through). Note that `redis_config` keys are passed to `redis.Redis` unchanged.
2. The `self.config = LoggingConfig(...)` rebuild (`redis_handler.py:83-90`) stays but now uses the widened `RedisConfig` from Task 1, so `RedisConfig(**raw)` accepts the full option surface. No structural change needed here beyond Task 1 — verify the rebuild still compiles and `self.client_config = raw` (`redis_handler.py:91`) is unchanged.

In `src/scietex/logging/valkey_handler.py`:

1. The `self.config = LoggingConfig(...)` rebuild (`valkey_handler.py:85-94`) stays; `ValkeyConfig(addresses=...)` is unchanged. No structural change needed beyond confirming `self.client_config` (`valkey_handler.py:80-84`) is unchanged.

In `src/scietex/logging/async_logging_handler.py`:

1. Confirm `self.error_handler = self.config.error_handler` (`:138`) and `self.queue_maxsize = self.config.queue_maxsize` (`:139`) are the aliases; they already read from config. No change needed for the aliases themselves.
2. The formatter is constructed from `self.config.service_name`/`self.config.worker_id` (`:140-142`) — already config-driven. No change.

In `src/scietex/logging/basic_handler.py`:

1. Confirm `self.stdout_enable = self.config.stdout_enable` (`:76`) and the console registration gate `if self.stdout_enable:` (`:78`) read from config. No change needed.

**The actual code change in this task is minimal** because AR-008 already routes the aliases through `self.config`. The substantive change is Task 1 (widening `RedisConfig`) plus confirming every work-time read site uses `self.config`. If the executor finds any work-time read of a flat attribute that is NOT an alias of `self.config` (e.g. `self.client_config` is read by `connect()` at `redis_handler.py:105` — that is the raw client input, intentionally separate), leave it. The single-source guarantee is: **every handler option that has a `LoggingConfig` field is read from `self.config` at work time, never from a parallel copy that can drift.**

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_async_logging_handler.py::test_config_is_single_source_of_truth tests/test_redis_handler.py::test_redis_config_accepts_valid_extra_options -v`
Expected: PASS.

- [ ] **Step 5: Run the full non-live suite**

Run: `uv run pytest tests/test_config.py tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_message_broker_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py tests/test_console_backend.py tests/test_formatter.py tests/test_version.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/scietex/logging/redis_handler.py src/scietex/logging/valkey_handler.py tests/test_async_logging_handler.py tests/test_redis_handler.py
git commit -m "refactor: make LoggingConfig the single runtime source of truth (AR-016, AR-023)"
```

---

### Task 3: Update config docs and append Resolution notes

**Files:**
- Modify: `docs/configuration.md:142-159`
- Modify: `docs/backends.md:49-57,83-90`
- Modify: `docs/architecture/components.md:97-101,214-219,243-248`
- Modify: `docs/architecture/overview.md:42-45`
- Modify: `docs/architecture/lifecycle.md:7-31`
- Modify: `docs/reviews/architecture/2026-09-05.md` (Resolution notes)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `docs/configuration.md` typed-config section**

In `docs/configuration.md:142-159`, replace the "Typed Configuration" subsection. The current text says `RedisConfig` is `host/port/db` and that unknown `redis_config` keys raise `TypeError`. Update to state: `LoggingConfig` is the single runtime source of truth; `RedisConfig` mirrors the full redis client option surface; `redis_config` dict keys are passed through to `redis.Redis` unchanged (no longer rejected); `backend_config` is a union of `RedisConfig | ValkeyConfig | None`.

- [ ] **Step 2: Update `docs/backends.md` Redis/Valkey config sections**

In `docs/backends.md:49-57` (Redis Configuration), remove the "unknown keys in the dict raise `TypeError`" claim and note the dict is passed through to `redis.Redis` unchanged, with `RedisConfig` a faithful projection. In `docs/backends.md:83-90` (Valkey Configuration), keep the `GlideClientConfiguration` note; no behavioral change for Valkey.

- [ ] **Step 3: Update `docs/architecture/components.md`**

Update the `RedisConfig`/`ValkeyConfig`/`backend_config` descriptions in the machinery-base Configuration note (`components.md:97-101`) and the Redis/Valkey handler sections (`components.md:214-219,243-248`) to reflect the union type and the pass-through of the redis dict.

- [ ] **Step 4: Update `docs/architecture/overview.md`**

Update the typed-config note (`overview.md:42-45`) to state `LoggingConfig` is the single runtime source of truth.

- [ ] **Step 5: Update `docs/architecture/lifecycle.md`**

Update the construction section (`lifecycle.md:7-31`) to note each handler builds one `LoggingConfig` and reads `self.config.*` at work time; the flat attributes are aliases.

- [ ] **Step 6: Append Resolution notes to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, append Resolution notes after the AR-016 finding block (ends line 242), after AR-017 (ends line 260), and after AR-023 (ends line 358), following the AR-013/014/015 convention:

```markdown
**Resolution (AR-016):** Resolved. `LoggingConfig` is now the single runtime
source of truth: every handler option that has a `LoggingConfig` field is read
from `self.config` at work time, and the flat attributes (`queue_maxsize`,
`stdout_enable`, `error_handler`) are thin aliases over `self.config`. The
post-hoc `LoggingConfig(...)` rebuilds in the concrete handlers were retained
but now run once per handler and attach a faithful `backend_config`. See the
P2 plan (2026-09-05-p2-architecture-refactors.md) for the config decision.

**Resolution (AR-017):** Resolved. `RedisConfig` now mirrors the full redis
client option surface (password, username, ssl family, timeouts, encoding,
health_check_interval, etc.), so `RedisConfig(**raw)` no longer raises
`TypeError` on legitimate options. The `redis_config` dict is passed through to
`redis.Redis(**self.client_config)` unchanged; `RedisConfig` is a faithful
readable projection, not the client input.

**Resolution (AR-023):** Resolved. `LoggingConfig.backend_config` is now typed
as a real union `RedisConfig | ValkeyConfig | None` instead of `Any`. Backend
config is attached once per handler via a single `LoggingConfig` rebuild (or
`dataclasses.replace`), eliminating the double representation where Redis kept
both a raw `client_config` dict and a typed `backend_config` that shadowed it.
```

- [ ] **Step 7: Commit**

```bash
git add docs/configuration.md docs/backends.md docs/architecture/components.md docs/architecture/overview.md docs/architecture/lifecycle.md docs/reviews/architecture/2026-09-05.md
git commit -m "docs: document single-source LoggingConfig and full redis option surface (AR-016, AR-017, AR-023)"
```

---

## Workstream 2: AR-018 — Decouple broker wire format from `ScietexFormatter` internals

### Task 4: Derive broker `name`/`time` from config + record, not the formatter

**Files:**
- Modify: `src/scietex/logging/message_broker_handler.py:155-168`
- Test: `tests/test_message_broker_handler.py`
- Docs: `docs/architecture/data-flow.md:36-52`, `docs/architecture/components.md:192-198`, `docs/architecture/hotspots.md:209-229`, `docs/reviews/architecture/2026-09-05.md` (Resolution note)

**Interfaces:**
- Consumes: `self.config.service_name`, `self.config.worker_id` (from Workstream 1), the `logging.LogRecord` (for `record.created` and `record.name`).
- Produces: The broker dict's `name` field is `f"{self.config.service_name}:{self.config.worker_id}"` (identity from config, not the formatter); the `time` field is ISO-8601 UTC derived from `record.created` directly (not `self.formatter.formatTime`). The broker output is now invariant under `setFormatter(plain logging.Formatter)`.

**Background (why):** `AsyncBrokerHandler._worker` builds the broker dict with `name = getattr(self.formatter, "worker_name", record.name)` (`message_broker_handler.py:160`) and `time = self.formatter.formatTime(record)` (`:165-167`), while `level` and `message` are computed independently from the record (`:159,163`). The handler's identity (`service_name:worker_id`) lives only inside the *formatter* (`formatter.py:71`), not on the handler/config. `setFormatter(plain logging.Formatter)` (a supported API, `basic_handler.py:91-105`) silently changes broker output: `worker_name` is absent → `name` falls back to the logger name (identity lost), and `formatTime` degrades from ISO-8601 UTC to strftime. A rendering substitution should not alter the broker payload.

**Decision (recommended):** Derive `name` from `self.config` (identity) and `time` from `record.created` (ISO-8601 UTC), matching exactly what `ScietexFormatter.formatTime` produces today (`formatter.py:85-88`: `datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()`). This keeps the current output byte-identical for the default formatter while removing the formatter dependency. The `level_abbreviation` import (`message_broker_handler.py:12`) stays (relocating it is AR-026, out of scope).

- [ ] **Step 1: Write the failing test (broker output invariant under setFormatter)**

Append to `tests/test_message_broker_handler.py`:

```python
@pytest.mark.asyncio
async def test_broker_output_invariant_under_plain_formatter():
    """setFormatter(plain logging.Formatter) must not change the broker payload."""
    import logging as _logging

    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
    )
    # A plain formatter has no worker_name attribute and strftime formatTime.
    handler.setFormatter(_logging.Formatter("%(message)s"))

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(handler.sent))
    entry = handler.sent[0]
    # Identity still comes from config, not the (now plain) formatter.
    assert entry["name"] == "TestService:1"
    # time is still ISO-8601 UTC, not strftime.
    assert "T" in entry["time"] and entry["time"].endswith("+00:00")

    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_message_broker_handler.py::test_broker_output_invariant_under_plain_formatter -v`
Expected: FAIL — with a plain formatter, `getattr(self.formatter, "worker_name", record.name)` returns `record.name` (the logger name, e.g. "TestLogger"), not `"TestService:1"`, and `self.formatter.formatTime(record)` uses strftime (no `T`, no `+00:00`).

- [ ] **Step 3: Implement**

In `src/scietex/logging/message_broker_handler.py`, replace the field computation block (`:155-168`). The `datetime`/`timezone` imports are already present (`:7`).

Current (`:155-168`):
```python
                # Compute the broker fields directly instead of reading formatter-mutated
                # record attributes, keeping output deterministic regardless of stdout_enable.
                level = level_abbreviation(record.levelno)
                name = getattr(self.formatter, "worker_name", record.name)
                log_entry: dict[str, str] = {
                    "level": level,
                    "message": record.getMessage(),
                    "name": name,
                    "time": self.formatter.formatTime(record)
                    if self.formatter
                    else datetime.now(timezone.utc).isoformat(),
                }
```

New:
```python
                # Build the broker fields from config (identity) and the record
                # (level/message/time), never from the formatter. A setFormatter()
                # swap to a plain logging.Formatter must not change the broker
                # payload: identity lives on the config, and the timestamp is
                # ISO-8601 UTC derived from record.created directly.
                level = level_abbreviation(record.levelno)
                log_entry: dict[str, str] = {
                    "level": level,
                    "message": record.getMessage(),
                    "name": f"{self.config.service_name}:{self.config.worker_id}",
                    "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                }
```

Note: this produces byte-identical output to the current default path (`ScietexFormatter.formatTime` with no `datefmt` returns exactly `datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()`, `formatter.py:85-88`). The `datetime`/`timezone` imports remain used. Verify `self.config` is available on the broker handler (it is — set by `AsyncLoggingHandler.__init__`, `async_logging_handler.py:132`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_message_broker_handler.py::test_broker_output_invariant_under_plain_formatter -v`
Expected: PASS.

- [ ] **Step 5: Run the full broker + lifecycle test files (coordination check)**

Run: `uv run pytest tests/test_message_broker_handler.py tests/test_restartable_lifecycle.py tests/test_basic_handler.py -v`
Expected: PASS. Confirm `test_broker_output_deterministic_without_console` (`test_message_broker_handler.py:193-210`) still passes — it asserts `entry["name"] == "TestService:1"`, which the config-derived identity preserves.

- [ ] **Step 6: Update docs**

In `docs/architecture/data-flow.md:36-52` (Flow 2), update the dict-field description: `name` is now `f"{service_name}:{worker_id}"` from config, `time` is ISO-8601 UTC from `record.created` — both independent of the formatter. In `docs/architecture/components.md:192-198`, update the log-entry dict shape note. In `docs/architecture/hotspots.md:209-229` (hotspot 11), update the "broker dict built independently" note to say identity comes from config, not the formatter.

- [ ] **Step 7: Append Resolution note to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, after the AR-018 finding block (ends line 284):

```markdown
**Resolution (AR-018):** Resolved. The broker worker now derives `name` from
`self.config` (`f"{service_name}:{worker_id}"`) and `time` from `record.created`
(ISO-8601 UTC), never from the formatter. `setFormatter(plain logging.Formatter)`
no longer changes the broker payload: identity is a first-class config attribute
and the timestamp is computed directly. Output is byte-identical to the prior
default path. This precedes any AR-024 formatter-injection work, per the review's
cross-cutting note. Added `test_broker_output_invariant_under_plain_formatter`.
```

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/message_broker_handler.py tests/test_message_broker_handler.py docs/architecture/data-flow.md docs/architecture/components.md docs/architecture/hotspots.md docs/reviews/architecture/2026-09-05.md
git commit -m "refactor: derive broker name/time from config and record, not formatter (AR-018)"
```

---

## Workstream 3: AR-019 — Coordinator-owned drain results; console as explicit post-drain observer

### Task 5: Drain hooks return their `BackendDrainResult`; `stop_logging` owns collection

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:46,153-181,250-304`
- Modify: `src/scietex/logging/message_broker_handler.py:199-222`
- Modify: `src/scietex/logging/console_backend.py:102-131`
- Modify: `src/scietex/logging/basic_handler.py:84-89`
- Test: `tests/test_async_logging_handler.py`, `tests/test_message_broker_handler.py`, `tests/test_console_backend.py`
- Docs: `docs/architecture/data-flow.md:67-84`, `docs/architecture/lifecycle.md:64-82`, `docs/architecture/components.md:74-89,118-126`, `docs/architecture/hotspots.md:29-45`, `docs/advanced.md:46-63`, `docs/reviews/architecture/2026-09-05.md` (Resolution note)

**Interfaces:**
- Consumes: `BackendDrainResult`, `DrainStatus` (unchanged, `async_logging_handler.py:23-43`).
- Produces: `DrainHook = Callable[[float], Awaitable[BackendDrainResult]]` — a drain hook takes only the timeout and **returns** its `BackendDrainResult`. `stop_logging` calls each hook, collects the returned results into a local list, then invokes an explicit post-drain observer (the console status reporter) with the collected results. The console is no longer a drain hook that reads a shared mutable list mid-iteration.

**Background (why):** `stop_logging` does `results: list[...] = []; for drain in reversed(self._drain_hooks): await drain(timeout, results)` (`async_logging_handler.py:279-281`). The console observes broker outcomes only via registration order (console first, brokers after) plus reverse iteration — emergent, undocumented, unenforced. With `stdout_enable=False`, no hook reads `results` (wasted bookkeeping). Adding a third backend or a second status reporter silently shifts semantics (AR-019).

**Design:** The coordinator (`stop_logging`) owns result collection. Each backend's drain hook returns its own `BackendDrainResult`. The console's status reporting is split out of its drain hook into an explicit `report_status(results)` method that `stop_logging` calls after collecting all backend results (only when a console backend is registered). This removes the reverse-order coupling: `stop_logging` drains each backend (any order), collects results, then reports them.

Concretely:
- `DrainHook` type alias becomes `Callable[[float], Awaitable[BackendDrainResult]]` (`async_logging_handler.py:46`).
- `AsyncBrokerHandler.drain(timeout)` returns `BackendDrainResult` instead of appending to a shared list (`message_broker_handler.py:199-222`).
- `ConsoleBackend.drain(timeout)` returns its own `BackendDrainResult` (drain its own queue) and does NOT read a shared `results` list. A new `ConsoleBackend.report_status(results: list[BackendDrainResult])` method enqueues the synthetic status records.
- `AsyncBaseHandler` registers the console backend's `drain` hook AND, separately, registers the console as the status reporter. `stop_logging` collects all drain results, then calls the registered status reporter (if any) with them.

**How the console stays the status reporter without reverse-order coupling:** The cleanest mechanism is a separate `_status_reporters` list on the machinery base, registered by `AsyncBaseHandler` alongside the console backend. `stop_logging` drains all backends (collecting results), then invokes each status reporter with the full results list. This makes the console an explicit post-drain observer rather than a drain hook that happens to run last.

- [ ] **Step 1: Write the failing test (drain hooks return results; console is a post-drain observer)**

Update `tests/test_console_backend.py::test_drain_queues_status_records_for_each_outcome` (`:98-119`) — it currently calls `backend.drain(timeout=5, results=results)` with a pre-built results list. Under the new contract, `drain` takes only `timeout` and returns a result; status reporting moves to `report_status(results)`.

New test:
```python
@pytest.mark.asyncio
async def test_report_status_queues_synthetic_records(capsys):
    """report_status() surfaces every backend's outcome as a synthetic status record."""
    running_event = asyncio.Event()
    running_event.set()
    backend = ConsoleBackend(FakeFormatter(), running_event)
    worker = asyncio.create_task(backend._worker())

    results = [
        BackendDrainResult(name="redis", status=DrainStatus.COMPLETED),
        BackendDrainResult(name="valkey", status=DrainStatus.TIMEOUT),
        BackendDrainResult(name="broker", status=DrainStatus.ERROR, error=RuntimeError("boom")),
    ]
    await backend.report_status(results)

    captured = capsys.readouterr().out
    assert "Redis Logger has completed processing its queue." in captured
    assert "Timeout while waiting for valkey logger to complete its queue." in captured
    assert "Error while waiting for broker Logger: boom" in captured

    running_event.clear()
    await worker
```

Append to `tests/test_async_logging_handler.py` a test that `stop_logging` collects results and invokes the status reporter:

```python
@pytest.mark.asyncio
async def test_stop_logging_collects_results_and_reports():
    """stop_logging drains each backend, collects results, then reports them."""
    from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus

    reported = []

    class ReportingHandler(AsyncLoggingHandler):
        def __init__(self):
            super().__init__()
            self.register_backend(
                "a", asyncio.Queue(), self._noop_worker, self._drain_a
            )
            self.register_backend(
                "b", asyncio.Queue(), self._noop_worker, self._drain_b
            )
            self.register_status_reporter(lambda results: reported.append(list(results)))

        async def _noop_worker(self):
            while self.logging_running_event.is_set():
                await asyncio.sleep(0.01)

        async def _drain_a(self, timeout):
            return BackendDrainResult("a", DrainStatus.COMPLETED)

        async def _drain_b(self, timeout):
            return BackendDrainResult("b", DrainStatus.TIMEOUT)

    handler = ReportingHandler()
    await handler.start_logging()
    await handler.stop_logging(timeout=0.1)

    names = [r.name for r in reported[0]]
    assert names == ["a", "b"]  # both backend results collected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_console_backend.py::test_report_status_queues_synthetic_records tests/test_async_logging_handler.py::test_stop_logging_collects_results_and_reports -v`
Expected: FAIL — `ConsoleBackend` has no `report_status` method, `drain` still takes a `results` arg, and `AsyncLoggingHandler` has no `register_status_reporter`.

- [ ] **Step 3: Implement the coordinator-owned drain**

In `src/scietex/logging/async_logging_handler.py`:

1. Change the `DrainHook` type alias (`:46`):
```python
DrainHook = Callable[[float], Awaitable[BackendDrainResult]]
```

2. Add a status-reporter registration seam. Add a `_status_reporters: list[Callable[[list[BackendDrainResult]], Awaitable[None]]]` list in `__init__` (`:148` area) and a method:
```python
    def register_status_reporter(
        self, reporter: Callable[[list[BackendDrainResult]], Awaitable[None]]
    ) -> None:
        """Register an async post-drain observer invoked with all backend drain results."""
        self._status_reporters.append(reporter)
```

3. Update `register_backend`'s `drain` docstring (`:175-176`) to the new signature `drain(timeout) -> BackendDrainResult`.

4. Rewrite the drain section of `stop_logging` (`:276-281`). Current:
```python
        # Drain every backend generically while the workers are still running. Results
        # are collected so a backend that reports shutdown status (e.g. the console)
        # can observe how each other backend fared before it drains itself.
        results: list[BackendDrainResult] = []
        for drain in reversed(self._drain_hooks):
            await drain(timeout, results)
```
New:
```python
        # Drain every backend generically while the workers are still running. The
        # coordinator owns result collection: each drain hook returns its own
        # BackendDrainResult, and the collected results are handed to the registered
        # status reporters (e.g. the console) as an explicit post-drain step. This
        # removes the reverse-registration-order coupling between a status reporter
        # and the backends it reports on.
        results: list[BackendDrainResult] = []
        for drain in self._drain_hooks:
            results.append(await drain(timeout))
        for reporter in self._status_reporters:
            await reporter(results)
```

Note: the `reversed()` is removed — order no longer matters because the console is not a drain hook that must run last; it is a separate reporter invoked after all drains. Update the `stop_logging` docstring (`:254-258`) and the `register_backend` docstring (`:166-168`) that describe the reverse-order semantics.

- [ ] **Step 4: Update the broker drain hook to return its result**

In `src/scietex/logging/message_broker_handler.py`, rewrite `drain` (`:199-222`). Current appends to a shared `results` list. New returns the result:

```python
    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the broker queue and return the outcome for status reporting.

        Waits for every queued record to be acknowledged by the worker, then returns
        a result describing how the drain concluded so the coordinator can surface it
        to the registered status reporters (e.g. the console).

        Args:
            timeout (float): Timeout for the queue to drain.

        Returns:
            BackendDrainResult: How the drain concluded.
        """
        try:
            await asyncio.wait_for(self.log_queues[self.queue_name].join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult(self.queue_name, DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult(self.queue_name, DrainStatus.ERROR, exc)
        else:
            return BackendDrainResult(self.queue_name, DrainStatus.COMPLETED)
```

- [ ] **Step 5: Update the console backend**

In `src/scietex/logging/console_backend.py`:

1. Rewrite `drain` (`:102-131`) to take only `timeout` and return the console's own result (drain its own queue). Move the status-record enqueueing into a new `report_status` method:
```python
    async def drain(self, timeout: float) -> BackendDrainResult:
        """
        Drain the console queue and return the outcome.

        Args:
            timeout (float): Timeout for draining the console queue.

        Returns:
            BackendDrainResult: How the console queue drained.
        """
        try:
            await asyncio.wait_for(self.queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult("console", DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult("console", DrainStatus.ERROR, exc)
        else:
            return BackendDrainResult("console", DrainStatus.COMPLETED)

    async def report_status(self, results: list[BackendDrainResult]) -> None:
        """
        Enqueue a synthetic status record for each other backend's drain outcome.

        Called by the coordinator after all backends have drained, so the console
        surfaces how every backend fared during shutdown. Status records are
        best-effort: when the console queue is full they are dropped rather than
        blocking shutdown on a bounded queue the worker may already be draining.

        Args:
            results (list[BackendDrainResult]): Drain outcomes from every backend.
        """
        for result in results:
            try:
                self.queue.put_nowait(_status_record(result))
            except asyncio.QueueFull:
                pass
```

2. Update the class docstring (`:47-60`) to describe `report_status` as the post-drain observer.

- [ ] **Step 6: Register the console as both a drain hook and a status reporter**

In `src/scietex/logging/basic_handler.py`, the console registration (`:84-89`) currently registers the console's `drain` hook. Add a `register_status_reporter` call so the console is invoked as a post-drain observer:

```python
            self.register_backend(
                "console",
                self._console_backend.queue,
                self._console_backend._worker,
                self._console_backend.drain,
            )
            self.register_status_reporter(self._console_backend.report_status)
```

- [ ] **Step 7: Run the affected test files**

Run: `uv run pytest tests/test_async_logging_handler.py tests/test_message_broker_handler.py tests/test_console_backend.py tests/test_basic_handler.py tests/test_restartable_lifecycle.py tests/test_queue_bounds.py -v`
Expected: PASS. Watch for any test that called `drain(timeout, results)` with the old two-arg signature — update those call sites to the new one-arg signature. `test_console_backend.py::test_drain_queues_status_records_for_each_outcome` must be replaced by `test_report_status_queues_synthetic_records` (Step 1).

- [ ] **Step 8: Run the full non-live suite**

Run: `uv run pytest tests/test_config.py tests/test_async_logging_handler.py tests/test_basic_handler.py tests/test_message_broker_handler.py tests/test_queue_bounds.py tests/test_restartable_lifecycle.py tests/test_console_backend.py tests/test_formatter.py tests/test_version.py -q`
Expected: PASS.

- [ ] **Step 9: Update docs**

In `docs/architecture/data-flow.md:67-84` (Flow 4), update the drain sequence: `stop_logging` drains each backend (hooks return results), collects them, then invokes the console status reporter. In `docs/architecture/lifecycle.md:64-82` (shutdown), update step 2 to describe coordinator-owned collection + post-drain reporting. In `docs/architecture/components.md:74-89` (machinery base) and `:118-126` (console backend), update the `drain`/`register_status_reporter` signatures. In `docs/architecture/hotspots.md:29-45` (hotspot 2), update the drain description. In `docs/advanced.md:46-63` (custom backend), update the `drain` hook signature in the custom-backend example to the one-arg returning form.

- [ ] **Step 10: Append Resolution note to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, after the AR-019 finding block (ends line 301):

```markdown
**Resolution (AR-019):** Resolved. `stop_logging` now owns result collection:
each backend's `drain(timeout)` hook returns its own `BackendDrainResult`, and
the collected results are handed to registered status reporters as an explicit
post-drain step. The console is no longer a drain hook that reads a shared
mutable list mid-iteration; it registers a `report_status(results)` observer via
the new `register_status_reporter` seam. Reverse-registration-order coupling is
removed. Added `test_report_status_queues_synthetic_records` and
`test_stop_logging_collects_results_and_reports`.
```

- [ ] **Step 11: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py src/scietex/logging/message_broker_handler.py src/scietex/logging/console_backend.py src/scietex/logging/basic_handler.py tests/test_async_logging_handler.py tests/test_message_broker_handler.py tests/test_console_backend.py docs/architecture/data-flow.md docs/architecture/lifecycle.md docs/architecture/components.md docs/architecture/hotspots.md docs/advanced.md docs/reviews/architecture/2026-09-05.md
git commit -m "refactor: coordinator-owned drain results with explicit console status reporter (AR-019)"
```

---

## Task 6: Final verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full verification command set**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run ty check src/scietex/logging/
```
Expected: all tests pass (68+), ruff clean, ty clean. Note: the Redis e2e test (`tests/test_redis_handler.py::test_redis_handler_logs_to_stream`) requires a live Redis on localhost:6379 and is not skip-guarded (AR-029, out of scope); if no Redis is running, run the non-live suite instead and note the Redis e2e test as environment-dependent.

- [ ] **Step 2: Re-read the modified files end-to-end**

Read `src/scietex/logging/config.py`, `async_logging_handler.py` (drain section), `message_broker_handler.py` (worker field block + drain), `console_backend.py` (drain + report_status), `redis_handler.py`, `valkey_handler.py`, `basic_handler.py`. Confirm: no leftover `reversed(self._drain_hooks)`; no drain hook still takes a `results` arg; no `getattr(self.formatter, "worker_name", ...)` or `self.formatter.formatTime` in the broker worker; no `RedisConfig(**raw)` that can reject a valid option; no commented-out code.

- [ ] **Step 3: Grep for broken callers**

Run: `rg "drain\(|_drain_hooks|formatTime|worker_name|backend_config|register_status_reporter|reversed\(" src/scietex/logging/ tests/`
Confirm no caller still uses the old two-arg `drain(timeout, results)` signature, no broker worker reads the formatter for `name`/`time`, and `backend_config` is typed as a union.

---

## Ordering Dependencies

- **Workstream 1 (config, Tasks 1-3) first.** Task 1 (widen `RedisConfig`, union type) is the foundation; Task 2 confirms the single-source reads; Task 3 is docs. Task 4 (AR-018) reads `self.config.service_name`/`worker_id`, so it depends on Workstream 1 being in place (the config fields already exist, but the single-source guarantee makes the identity authoritative). Task 5 (AR-019) is independent of both.
- **Task 4 (AR-018) and Task 5 (AR-019) are independent of each other** and of the config refactor's *code* (AR-018 reads `self.config` which already exists; AR-019 touches the drain seam only). They can be parallelized after Task 1 lands. However, both touch `message_broker_handler.py` (Task 4 edits the worker field block `:155-168`; Task 5 edits `drain` `:199-222`) — different regions, so no edit conflict, but run them sequentially if a single executor is used to avoid overlapping edits to the same file.
- **Task 6** is the final gate.

## Test Strategy

- **AR-017/023 (Task 1):** `test_redis_config_accepts_full_client_option_surface` (RedisConfig accepts password/username/ssl/etc.); `test_backend_config_is_a_union_not_any` (type hint is the union).
- **AR-016 (Task 2):** `test_config_is_single_source_of_truth` (aliases mirror config); `test_redis_config_accepts_valid_extra_options` (a dict with password/ssl no longer raises).
- **AR-018 (Task 4):** `test_broker_output_invariant_under_plain_formatter` (name/time unchanged after `setFormatter(plain logging.Formatter)`); existing `test_broker_output_deterministic_without_console` must still pass.
- **AR-019 (Task 5):** `test_report_status_queues_synthetic_records` (replaces the old two-arg drain test); `test_stop_logging_collects_results_and_reports` (coordinator collects + reports). Existing broker/console drain tests updated to the one-arg returning signature.
- All tests use the existing `_wait_for` predicate-polling helper and `FakeBrokerHandler`/`CountingQueue` fakes — no live Redis/Valkey needed except the pre-existing e2e tests.

## Risks / Decisions Needing Sign-off

1. **Config decision (AR-016/017/023):** Option A (single-source `LoggingConfig`) is recommended. Option B (drop the typed layer) is a larger deletion that discards AR-008's loud-unknown-kwarg guarantee and removes the natural carrier for AR-018's identity. **Confirm Option A before Task 1.**
2. **`RedisConfig` field-set breadth (AR-017):** The widened `RedisConfig` must be a superset of the redis client options users realistically pass. If a user passes an option not declared, `RedisConfig(**raw)` still raises. The plan covers the common surface (password, username, ssl family, timeouts, encoding, retry, health_check, client_name, protocol) but not every redis 8.x option (object-valued ones like `connection_pool`/`credential_provider` are excluded). **Decision:** confirm the declared superset is acceptable, or prefer to drop the `RedisConfig(**raw)` conversion entirely and store the raw dict as `backend_config` (a `dict`-typed union member) to guarantee no rejection. The latter is more robust but less "typed".
3. **AR-018 output byte-identity:** The fix derives `time` as `datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()`, which is byte-identical to `ScietexFormatter.formatTime`'s no-datefmt path. If a user had set a custom `datefmt` on the formatter and relied on the broker `time` reflecting it, that behavior changes (broker `time` is now always ISO-8601 UTC). This is the intended fix (the review calls the formatter-dependent `time` a defect), but flag it as a behavior change for anyone who customized `datefmt` and expected it in the broker payload.
4. **AR-019 console status ordering:** Previously the console (registered first) drained last and reported other backends' outcomes. Under the new design, `stop_logging` drains all backends, collects results, then calls `report_status`. The console's own drain result is now included in the reported set (it was not before, since the console drained after reporting). This means the console may print its own "Console Logger has completed processing its queue." status record. **Decision:** confirm including the console's own result in the status report is acceptable, or filter it out in `report_status` (skip a result whose `name == "console"`).
5. **`register_status_reporter` is a new public method** on `AsyncLoggingHandler`. It needs a caller (the console registration in `basic_handler.py`) and a test — both provided. It is the documented extension seam for third-party status reporters.

## Handoff Plan

1. Execute Task 1 (config.py): widen `RedisConfig`, type `backend_config` as `RedisConfig | ValkeyConfig | None`; add the two config tests. Verify: `uv run pytest tests/test_config.py -v`.
2. Execute Task 2 (config threading): confirm handlers read `self.config.*` at work time; add `test_config_is_single_source_of_truth` and `test_redis_config_accepts_valid_extra_options`. Verify: the two new tests + full non-live suite.
3. Execute Task 3 (config docs): update `docs/configuration.md`, `docs/backends.md`, `docs/architecture/{components,overview,lifecycle}.md`; append AR-016/017/023 Resolution notes.
4. Execute Task 4 (AR-018): rewrite the broker worker field block in `message_broker_handler.py:155-168` to derive `name`/`time` from config+record; add `test_broker_output_invariant_under_plain_formatter`; update data-flow/components/hotspots docs + AR-018 Resolution note.
5. Execute Task 5 (AR-019): coordinator-owned drain in `async_logging_handler.py`; broker `drain` returns result; console `report_status` observer; register console as status reporter in `basic_handler.py`; update the affected tests + docs + AR-019 Resolution note.
6. Execute Task 6: full verification command set.
- Risk: Task 4 and Task 5 both edit `message_broker_handler.py` — run sequentially if one executor. Task 5 changes the `drain` signature, so update every `drain(timeout, results)` call site (broker, console, and any test fake). Confirm Option A (config decision) before Task 1.
- Test: `uv run pytest -q && uv run ruff check . && uv run ty check src/scietex/logging/` all green; the new per-finding tests pass individually.
