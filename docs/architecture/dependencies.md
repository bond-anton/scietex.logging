# Dependencies

This document focuses on **architectural** dependency relationships and
direction, not an exhaustive list of third-party packages.

## Intra-package dependency graph

```
formatter.py
   ▲
   │ (imports ScietexFormatter)
basic_handler.py ──────────────┐
   ▲                           │ (extends AsyncBaseHandler)
   │ (imports AsyncBaseHandler)│
message_broker_handler.py ─────┘
   ▲
   │ (extends AsyncBrokerHandler)
   ├── redis_handler.py
   └── valkey_handler.py
   ▲
   │ (guarded imports)
__init__.py  (public API)
```

Direction is strictly **one-way, top-down**: `formatter` → `basic_handler` →
`message_broker_handler` → concrete backends → `__init__`. There are **no
circular dependencies** within the package.

## Core → infrastructure dependencies

The package has a clean layering where "core" (queue/worker/event machinery in
`AsyncBaseHandler`) depends only on the stdlib and on its own formatter, and
never on any concrete backend or third-party client.

- **Core** (`AsyncBaseHandler`) → stdlib `asyncio`, `logging`, `sys`; → own
  `ScietexFormatter`. No third-party runtime deps.
- **Broker abstraction** (`AsyncBrokerHandler`) → core; stdlib `asyncio`,
  `datetime`. No third-party runtime deps.
- **Concrete backends** (`AsyncRedisHandler`, `AsyncValkeyHandler`) → broker
  abstraction + their respective third-party clients (`redis`, `glide`).
- **Public API** (`__init__.py`) → all modules, but the two concrete-backend
  imports are guarded so the core never hard-depends on optional clients.

This is the intended **core → infrastructure** boundary: the async core is
backend-agnostic; third-party infrastructure (Redis/Valkey clients) is confined
to the leaf backend modules.

## Cross-module dependencies

- `basic_handler.py` → `formatter.py` (constructs `ScietexFormatter`).
- `message_broker_handler.py` → `basic_handler.py` (inheritance + reuse of
  queues/events/workers).
- `redis_handler.py`, `valkey_handler.py` → `message_broker_handler.py`
  (inheritance + implement abstract methods).
- `__init__.py` → all of the above (re-export).

## Circular dependencies

None detected. The import graph is acyclic and strictly layered.

## Important dependency chains

1. **Logging call chain (runtime):**
   `logging.Logger` → `AsyncBaseHandler.emit` → `asyncio.Queue` → worker
   coroutine → `ScietexFormatter` → backend sink. This is the primary data
   path (see data-flow.md).

2. **Class hierarchy chain (compile/design time):**
   `logging.Handler` → `AsyncBaseHandler` → `AsyncBrokerHandler` →
   `AsyncRedisHandler` / `AsyncValkeyHandler`. Each level adds one concern:
   stdlib integration → async machinery + console → broker abstraction →
   concrete transport.

3. **Optional-dependency chain (packaging):**
   `pyproject.toml` extras (`[redis]`, `[valkey]`, `[all]`) → third-party
   clients → guarded imports in `redis_handler.py` / `valkey_handler.py` →
   guarded re-exports in `__init__.py`. The guard chain is what keeps the base
   install dependency-free.

## Third-party runtime dependencies (by module)

| Module | Third-party dep | Optional? |
|---|---|---|
| `basic_handler.py`, `formatter.py`, `message_broker_handler.py` | none | — |
| `redis_handler.py` | `redis>=5.0.0` | yes (`[redis]`) |
| `valkey_handler.py` | `valkey-glide~=2.5.0` | yes (`[valkey]`) |

## Dev / tooling dependencies (not runtime)

`tox` (format/lint/type/py314 envs), `ruff`, `pytest`, `pytest-asyncio`,
`pytest-sugar`, `coverage`, `ty` (type checker). Note: `tox.ini` pins
`valkey-glide~=2.2.0` in the `type` and default envs while `pyproject.toml`
declares `~=2.5.0` — a version skew between tox and packaging config
(see hotspots.md).

## External infrastructure dependencies (runtime)

- **Redis** server (for `AsyncRedisHandler`).
- **Valkey** server (for `AsyncValkeyHandler`).

These are external services the host application must provide; the package
only opens client connections to them.
