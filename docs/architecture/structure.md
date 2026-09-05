# Repository / Package Structure

## Top-level layout

```
scietex.logging/
├── src/scietex/logging/        # the package (all runtime code)
├── tests/                      # pytest suite
├── examples/                   # runnable usage scripts
├── docs/                       # user-facing documentation
│   └── architecture/           # this map
├── .github/workflows/          # CI (lint, package/test, publish)
├── pyproject.toml              # build + packaging + extras
├── tox.ini                     # tox envs: format, lint, type, py314
├── pytest.ini                  # pytest config
├── .ruff.toml                  # ruff config
├── uv.lock                     # locked deps (uv)
├── README.md                   # user-facing readme
└── AGENTS.md                   # agent guidance
```

## Package: `src/scietex/logging/`

| Module | Responsibility |
|---|---|
| `__init__.py` | Public API. Re-exports `AsyncBaseHandler`, `AsyncBrokerHandler`, `AsyncLoggingHandler`, `ConsoleBackend`, `ScietexFormatter`; conditionally adds `AsyncRedisHandler` / `AsyncValkeyHandler`; defines `__version__ = "0.2.0"`. |
| `async_logging_handler.py` | `AsyncLoggingHandler` — pure shared async machinery (queues/events/workers, `register_backend`, `start_logging`/`emit`/`stop_logging`, error channel); no sink of its own. |
| `console_backend.py` | `ConsoleBackend` — the console (stdout) sink as a peer backend (queue + worker + drain hook). |
| `basic_handler.py` | `AsyncBaseHandler` — thin concrete subclass of `AsyncLoggingHandler` that registers the console backend as a peer when `stdout_enable=True`. |
| `formatter.py` | `ScietexFormatter` (`logging.Formatter` subclass) + `level_abbreviation` helper. |
| `config.py` | Typed config objects (`LoggingConfig`, `RedisConfig`, `ValkeyConfig`) + `validate_queue_maxsize` / `optional_dependency_error` helpers. Stdlib-only leaf module. |
| `message_broker_handler.py` | `AsyncBrokerHandler` — abstract broker backend base (registers queue + worker; connect/disconnect/send_message contract). |
| `redis_handler.py` | `AsyncRedisHandler` — Redis stream backend via `redis.asyncio`. |
| `valkey_handler.py` | `AsyncValkeyHandler` — Valkey stream backend via `valkey-glide`. |
| `py.typed` | Marker file (empty) enabling PEP 561 type info. |

### Module dependency graph (imports)

```
formatter.py            (no intra-package imports)
config.py               (no intra-package imports)
async_logging_handler.py → formatter.py, config.py
console_backend.py      → async_logging_handler.py
basic_handler.py        → async_logging_handler.py, console_backend.py, config.py
message_broker_handler.py → basic_handler.py
redis_handler.py        → message_broker_handler.py, config.py
valkey_handler.py       → message_broker_handler.py, config.py
__init__.py             → basic_handler.py, formatter.py,
                          message_broker_handler.py,
                          redis_handler.py (guarded), valkey_handler.py (guarded)
```

Dependency direction is strictly **top-down / one-way**: formatter ← machinery
← console/handler ← broker ← concrete backends ← `__init__`. There are no
cycles.

## Tests: `tests/`

| File | Covers |
|---|---|
| `test_async_logging_handler.py` | `AsyncLoggingHandler` machinery: init, register_backend, start/stop, emit, error channel. |
| `test_basic_handler.py` | `AsyncBaseHandler` init, start/stop, emit→queue, console worker stdout, pending-task drain, cleanup threshold. |
| `test_message_broker_handler.py` | `AsyncBrokerHandler` queue/worker registration and drain behavior. |
| `test_config.py` | `LoggingConfig` / `RedisConfig` / `ValkeyConfig`, `validate_queue_maxsize`, `optional_dependency_error`. |
| `test_formatter.py` | `level_abbreviation`, `ScietexFormatter.formatTime` (ISO UTC), `format` (worker name + level abbrev). |
| `test_console_backend.py` | `ConsoleBackend` queue/worker/drain and shutdown-status reporting. |
| `test_queue_bounds.py` | Bounded-queue overflow policy (drop + report). |
| `test_restartable_lifecycle.py` | Multiple start/stop cycles on the same event loop. |
| `test_redis_handler.py` | End-to-end Redis stream write (requires live Redis on localhost:6379). |
| `test_valkey_handler.py` | End-to-end Valkey stream write (requires live Valkey on localhost:6379). |
| `test_version.py` | `__version__` format sanity (unittest-style). |

Note: `test_redis_handler.py` is an integration test that requires a running
Redis server and is **not** skipped when the server is absent (it would fail).
`test_valkey_handler.py` carries a connectivity-probe skip guard
(`@pytest.mark.skipif` via a `_valkey_server_reachable()` socket probe), so it
skips cleanly when no Valkey server is reachable. CI (`python-package.yml`)
provisions a Redis service container but **not** a Valkey one.

## Examples: `examples/`

| File | Demonstrates |
|---|---|
| `basic_console_logging.py` | Console-only logging. |
| `redis_logging.py` | Redis stream logging. |
| `valkey_logging.py` | Valkey stream logging. |
| `console_and_redis_logging.py` | Two handlers (console + Redis) on one logger. |
| `custom_formatter.py` | Using a custom formatter. |
| `error_handler_and_queue_bounds.py` | Error handler callback and bounded-queue overflow behavior. |
| `custom_backend.py` | Implementing a custom broker backend. |
| `pure_machinery_handler.py` | Using `AsyncLoggingHandler` machinery directly. |
| `restartable_lifecycle.py` | Multiple start/stop cycles on the same event loop. |
| `all_backends.py` | Console + Redis + Valkey together. |

## Docs: `docs/`

User-facing guides: `index.md` (overview/quick start), `configuration.md`,
`backends.md`, `advanced.md` (custom backends), `examples.md`. These describe
intended usage; the architecture map is code-derived and may differ from the
docs where docs are aspirational (e.g. `advanced.md` shows a PostgreSQL
backend that is not implemented).

## Notable boundaries

- **Optional-dependency boundary.** `redis_handler.py` and `valkey_handler.py`
  hard-import their third-party client at module top and raise a descriptive
  `ImportError` if missing. `__init__.py` guards these imports so the base
  package imports cleanly without extras. This is the main seam between
  "core" and "optional backends".
- **Extension boundary.** `AsyncBrokerHandler` is the intended extension point
  for new backends (per `__init__.py` docstring and `docs/advanced.md`): a
  subclass supplies `connect`, `disconnect`, `send_message`.
- **Stdlib boundary.** The package integrates with the standard `logging`
  framework only through `logging.Handler` (via `emit`) and `logging.Formatter`
  (via `format`). It does not define its own loggers or a logging entry point.
