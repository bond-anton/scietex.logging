# Dependencies

This document focuses on **architectural** dependency relationships and
direction, not an exhaustive list of third-party packages.

## Intra-package dependency graph

```
formatter/scietex.py      (imports: config.py)
formatter/json.py         (no intra-package imports)
config.py                 (no intra-package imports)
_executor.py              (no intra-package imports; provides _WriteExecutor)
async_logging_handler.py  (imports: config.py; no formatter dep)

   ▲ imports AsyncLoggingHandler
   ├── backend/console.py    (imports ConsoleBackend, _WriteExecutor)
   ├── backend/file.py       (imports FileBackend, _WriteExecutor)
   ├── handler/console.py    (extends AsyncLoggingHandler; registers console backend; imports ScietexFormatter)
   ├── handler/file.py       (extends AsyncLoggingHandler; registers "_file" backend; imports _WriteExecutor, ScietexFormatter)
   └── handler/broker.py     (extends AsyncLoggingHandler; abstract broker base)
          ▲ extends AsyncBrokerHandler
          ├── handler/redis.py
          ├── handler/valkey.py
          └── handler/mqtt.py
   ▲ guarded imports
__init__.py  (public API)
```

Direction is strictly **one-way, top-down** (no cycles): `config` is the shared
leaf; `async_logging_handler` depends only on `config`, while the console/file
handlers additionally import `formatter/scietex`; `backend/console`,
`backend/file`, `handler/console`, `handler/file`, and `handler/broker` extend or
import the base; concrete backends extend the broker base; `__init__` re-exports
everything.

## Core → infrastructure dependencies

The package has a clean layering where "core" (queue/worker/event machinery in
`AsyncLoggingHandler`) depends only on the stdlib, and never on any concrete
backend or third-party client.

- **Core** (`AsyncLoggingHandler`) → stdlib `asyncio`, `logging`. No
  third-party runtime deps. The formatter no longer belongs to the base — it
  moved to the console/file handlers.
- **Console peer** (`ConsoleBackend`) → core types (`BackendDrainResult`,
  `DrainStatus`); stdlib `asyncio`, `logging`, `sys`. No third-party runtime deps.
- **File peer** (`FileBackend`) → core types; stdlib `asyncio`, `logging`. No
  third-party runtime deps.
- **Concrete console handler** (`ConsoleHandler`) → core + console peer; it
  registers the console backend unconditionally.
- **Concrete file handler** (`AsyncFileHandler` + rotation variants) →
  core + file peer; registers the `"_file"` backend. No third-party runtime deps.
- **Broker abstraction** (`AsyncBrokerHandler`) → core; stdlib
  `asyncio`, `datetime`. No third-party runtime deps.
- **Concrete backends** (`AsyncRedisHandler`, `AsyncValkeyHandler`,
  `AsyncMqttHandler`) → broker abstraction + their respective third-party
  clients (`redis`, `glide`, `aiomqtt`).
- **Public API** (`__init__.py`) → all modules, but the three concrete-backend
  imports are guarded so the core never hard-depends on optional clients.

This is the intended **core → infrastructure** boundary: the async core is
backend-agnostic; third-party infrastructure (Redis/Valkey/MQTT clients) is
confined to the leaf broker backend modules. The file and console sinks are
stdlib-only peers, so they carry no optional dependency.

## Cross-module dependencies

- `async_logging_handler.py` → `config.py` (imports `LoggingConfig`,
  `validate_queue_maxsize`); no formatter import.
- `handler/console.py` → `formatter/scietex.py` (constructs `ScietexFormatter`).
- `handler/file.py` → `formatter/scietex.py` (constructs `ScietexFormatter`).
- `backend/console.py` → `async_logging_handler.py` (imports `BackendDrainResult`,
  `DrainStatus` for shutdown-status reporting) and `_executor.py` (imports
  `_WriteExecutor` to offload the blocking stdout write).
- `backend/file.py` → `async_logging_handler.py` (imports `BackendDrainResult`,
  `DrainStatus` for shutdown-status reporting) and `_executor.py` (imports
  `_WriteExecutor` to offload the blocking stream write).
- `handler/console.py` → `async_logging_handler.py` (inheritance) and
  `backend/console.py` (registers the console backend).
- `handler/file.py` → `async_logging_handler.py` (inheritance), `backend/file.py`
  (registers the `"_file"` backend), and `_executor.py` (imports `_WriteExecutor`
  for the worker's write unit and close-on-executor teardown).
- `handler/broker.py` → `async_logging_handler.py` (inheritance + reuse of
  queues/events/workers) and `config.py` (`level_abbreviation`).
- `handler/redis.py`, `handler/valkey.py`, `handler/mqtt.py` →
  `handler/broker.py` (inheritance + implement abstract methods).
- `__init__.py` → all of the above (re-export).

## Circular dependencies

None detected. The import graph is acyclic and strictly layered.

## Important dependency chains

1. **Logging call chain (runtime):**
   `logging.Logger` → `AsyncLoggingHandler.emit` → `asyncio.Queue` → worker
   coroutine → `ScietexFormatter` → backend sink. This is the primary data
   path (see data-flow.md).

2. **Class hierarchy chain (compile/design time):**
   `logging.Handler` → `AsyncLoggingHandler`, with `ConsoleHandler`,
   `AsyncFileHandler` (and its rotation variants), and `AsyncBrokerHandler` each
   subclassing `AsyncLoggingHandler` directly. `AsyncBrokerHandler` →
   `AsyncRedisHandler` / `AsyncValkeyHandler` / `AsyncMqttHandler`.
   `ConsoleBackend` and `FileBackend` are peer sinks registered by
   `ConsoleHandler` and `AsyncFileHandler` respectively. Each level adds one
   concern: stdlib integration → async machinery (no sink) → a single backend
   registration (console peer / file peer / broker abstraction) → concrete
   transport.

3. **Optional-dependency chain (packaging):**
   `pyproject.toml` extras (`[redis]`, `[valkey]`, `[mqtt]`, `[all]`) →
   third-party clients → guarded imports in `handler/redis.py` /
   `handler/valkey.py` / `handler/mqtt.py` → guarded re-exports in
   `__init__.py`. The guard chain is what keeps the base install
   dependency-free. Each backend module raises its descriptive `ImportError` via
   the shared `optional_dependency_error(module_name, extra)` helper in
   `config.py`, so the two-layer guard (module-level raise + package-level catch
   in `__init__.py`) cannot drift in message text.

## Third-party runtime dependencies (by module)

| Module | Third-party dep | Optional? |
|---|---|---|
| `_executor.py`, `async_logging_handler.py`, `handler/console.py`, `backend/console.py`, `backend/file.py`, `handler/file.py`, `formatter/scietex.py`, `formatter/json.py`, `handler/broker.py` | none | — |
| `handler/redis.py` | `redis>=5.0.0` | yes (`[redis]`) |
| `handler/valkey.py` | `valkey-glide~=2.5.0` | yes (`[valkey]`) |
| `handler/mqtt.py` | `aiomqtt~=2.5.0` | yes (`[mqtt]`) |

## Dev / tooling dependencies (not runtime)

`tox` (format/lint/type/py314 envs), `ruff`, `pytest`, `pytest-asyncio`,
`pytest-sugar`, `coverage`, `ty` (type checker). `tox.ini` and `pyproject.toml`
both pin `valkey-glide~=2.5.0` (see hotspots.md).

## External infrastructure dependencies (runtime)

- **Redis** server (for `AsyncRedisHandler`).
- **Valkey** server (for `AsyncValkeyHandler`).
- **MQTT** broker (for `AsyncMqttHandler`).

These are external services the host application must provide; the package
only opens client connections to them.
