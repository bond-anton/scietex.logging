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
| `__init__.py` | Public API. Re-exports `ConsoleHandler`, `AsyncBrokerHandler`, `AsyncLoggingHandler`, `AsyncFileHandler`, `AsyncRotatingFileHandler`, `AsyncTimedRotatingFileHandler`, `AsyncWatchedFileHandler`, `ConsoleBackend`, `FileBackend`, `JsonFormatter`, `ScietexFormatter`; conditionally adds `AsyncRedisHandler` / `AsyncValkeyHandler` / `AsyncMqttHandler`; defines `__version__ = "2.0.0"`. |
| `_executor.py` | `_WriteExecutor` — private single-thread executor helper offloading blocking write I/O off the event loop (lazy-create / run / `shutdown(wait=True)`). |
| `async_logging_handler.py` | `AsyncLoggingHandler` — pure shared async machinery (queues/events/workers, `register_backend`, `start_logging`/`emit`/`stop_logging`, error channel); no sink of its own. |
| `backend/console.py` | `ConsoleBackend` — the console (stdout) sink as a peer backend (queue + worker + drain hook). |
| `backend/file.py` | `FileBackend` — the file sink as a peer backend (queue + worker + drain hook), cloned from `ConsoleBackend` but writing to a `stream_provider()`-supplied file object. |
| `handler/console.py` | `ConsoleHandler` — thin concrete subclass of `AsyncLoggingHandler` that registers the console backend as a peer unconditionally. |
| `handler/file.py` | `AsyncFileHandler` — concrete subclass of `AsyncLoggingHandler` that registers the `"_file"` backend; plus rotation variants `AsyncRotatingFileHandler` / `AsyncTimedRotatingFileHandler` / `AsyncWatchedFileHandler` subclassing it. |
| `formatter/scietex.py` | `ScietexFormatter` (`logging.Formatter` subclass) + `level_abbreviation` helper. |
| `formatter/json.py` | `JsonFormatter` (`logging.Formatter` subclass) emitting single-line NDJSON. |
| `config.py` | Typed config objects (`LoggingConfig`, `RedisConfig`, `ValkeyConfig`, `MqttConfig`) + `validate_queue_maxsize` / `level_abbreviation` / `optional_dependency_error` / `report_error` helpers. Stdlib-only leaf module. |
| `handler/broker.py` | `AsyncBrokerHandler` — abstract broker backend base (registers queue + worker; connect/disconnect/send_message contract). |
| `handler/redis.py` | `AsyncRedisHandler` — Redis stream backend via `redis.asyncio`. |
| `handler/valkey.py` | `AsyncValkeyHandler` — Valkey stream backend via `valkey-glide`. |
| `handler/mqtt.py` | `AsyncMqttHandler` — MQTT topic backend via `aiomqtt`. |
| `py.typed` | Marker file (empty) enabling PEP 561 type info. |

### Module dependency graph (imports)

```
formatter/scietex.py      → config.py
formatter/json.py         (no intra-package imports)
config.py               (no intra-package imports)
_executor.py            (no intra-package imports)
async_logging_handler.py → config.py
backend/console.py       → async_logging_handler.py, _executor.py
backend/file.py          → async_logging_handler.py, config.py, _executor.py
handler/console.py       → async_logging_handler.py, backend/console.py, formatter/scietex.py
handler/file.py          → backend/file.py, async_logging_handler.py, _executor.py, formatter/scietex.py
handler/broker.py        → async_logging_handler.py, config.py
handler/redis.py         → handler/broker.py, config.py
handler/valkey.py        → handler/broker.py, config.py
handler/mqtt.py          → handler/broker.py, config.py
__init__.py              → handler/console.py, backend/file.py, handler/file.py,
                           formatter/scietex.py, formatter/json.py,
                           handler/broker.py,
                           handler/redis.py (guarded), handler/valkey.py (guarded),
                           handler/mqtt.py (guarded)
```

Dependency direction is strictly **top-down / one-way** (no cycles): `config`
is the shared leaf; `async_logging_handler` depends only on `config`, while the
console/file handlers additionally import `formatter/scietex`; broker handlers
extend the base; concrete backends extend the broker base; `__init__` re-exports
everything.

## Tests: `tests/`

| File | Covers |
|---|---|
| `test_async_logging_handler.py` | `AsyncLoggingHandler` machinery: init, register_backend, start/stop, emit, error channel. |
| `test_executor.py` | `_WriteExecutor` single-thread executor: off-loop run, write-then-close serialization, no-op shutdown, exception propagation, restartability. |
| `test_console_handler.py` | `ConsoleHandler` init, start/stop, emit→queue, console worker stdout, pending-task drain, cleanup threshold. |
| `test_message_broker_handler.py` | `AsyncBrokerHandler` queue/worker registration and drain behavior. |
| `test_client_injection.py` | `client=` injection into `AsyncBrokerHandler` (never-close ownership contract; injected client is not closed). |
| `test_config.py` | `LoggingConfig` / `RedisConfig` / `ValkeyConfig` / `MqttConfig`, `validate_queue_maxsize`, `optional_dependency_error`. |
| `test_formatter.py` | `level_abbreviation`, `ScietexFormatter.formatTime` (ISO UTC), `format` (level abbrev + `%(name)s` logger name). |
| `test_console_backend.py` | `ConsoleBackend` queue/worker/drain and shutdown-status reporting. |
| `test_file_backend.py` | `FileBackend` queue/worker/drain, dynamic stream provider, and shutdown-status reporting. |
| `test_json_formatter.py` | `JsonFormatter` single-line JSON output, extra flattening, exception handling, self-copying. |
| `test_file_handler.py` | `AsyncFileHandler` + rotation variants: file writes, append, JSON formatter, injected file-like, rollover. |
| `test_queue_bounds.py` | Bounded-queue overflow policy (drop + report). |
| `test_restartable_lifecycle.py` | Multiple start/stop cycles on the same event loop. |
| `test_redis_handler.py` | End-to-end Redis stream write (requires live Redis on localhost:6379). |
| `test_valkey_handler.py` | End-to-end Valkey stream write (requires live Valkey on localhost:6379). |
| `test_mqtt_handler.py` | `AsyncMqttHandler` unit tests + end-to-end MQTT publish (skipif-guarded on a live broker at localhost:1883). |
| `test_version.py` | `__version__` format sanity (unittest-style). |

Note: `test_redis_handler.py` is an integration test that requires a running
Redis server and is **not** skipped when the server is absent (it would fail).
`test_valkey_handler.py` carries a connectivity-probe skip guard
(`@pytest.mark.skipif` via a `_valkey_server_reachable()` socket probe), so it
skips cleanly when no Valkey server is reachable. `test_mqtt_handler.py` carries
the same connectivity-probe skip guard (`_mqtt_server_reachable()` socket probe
on localhost:1883), so it skips cleanly when no MQTT broker is reachable. CI
(`python-package.yml`) provisions Redis and MQTT (`eclipse-mosquitto`) service
containers but **not** a Valkey one.

## Examples: `examples/`

| File | Demonstrates |
|---|---|
| `basic_console_logging.py` | Console-only logging. |
| `file_logging.py` | Plain-text and JSON file logging. |
| `redis_logging.py` | Redis stream logging. |
| `valkey_logging.py` | Valkey stream logging. |
| `mqtt_logging.py` | MQTT topic logging. |
| `console_and_redis_logging.py` | Two handlers (console + Redis) on one logger. |
| `custom_formatter.py` | Using a custom formatter. |
| `error_handler_and_queue_bounds.py` | Error handler callback and bounded-queue overflow behavior. |
| `custom_backend.py` | Implementing a custom broker backend. |
| `pure_machinery_handler.py` | Using `AsyncLoggingHandler` machinery directly. |
| `restartable_lifecycle.py` | Multiple start/stop cycles on the same event loop. |
| `all_backends.py` | Console + Redis + Valkey together. |
| `injected_client.py` | Injecting an app-owned Valkey client into `AsyncValkeyHandler`. |

## Docs: `docs/`

User-facing guides: `index.md` (overview/quick start), `configuration.md`,
`backends.md`, `advanced.md` (custom backends), `examples.md`. These describe
intended usage; the architecture map is code-derived and may differ from the
docs where docs are aspirational (e.g. `advanced.md` shows a PostgreSQL
backend that is not implemented).

## Notable boundaries

- **Optional-dependency boundary.** `handler/redis.py`, `handler/valkey.py`,
  and `handler/mqtt.py` hard-import their third-party client at module top and
  raise a descriptive `ImportError` if missing. `__init__.py` guards these
  imports so the base package imports cleanly without extras. This is the main
  seam between "core" and "optional backends".
- **Extension boundary.** `AsyncBrokerHandler` is the intended extension point
  for new backends (per `__init__.py` docstring and `docs/advanced.md`): a
  subclass supplies `connect`, `disconnect`, `send_message`.
- **Stdlib boundary.** The package integrates with the standard `logging`
  framework only through `logging.Handler` (via `emit`) and `logging.Formatter`
  (via `format`). It does not define its own loggers or a logging entry point.
