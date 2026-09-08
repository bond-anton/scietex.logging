# AGENTS.md

This file provides guidance for OpenCode agents working on the `scietex.logging` repository.

## Project Overview

`scietex.logging` is an asynchronous Python logging package that provides non-blocking logging capabilities. It supports multiple backends including console logging, Redis logging, and Valkey logging.

## Key Details

- **Package Name**: `scietex.logging`
- **Python Requirement**: >=3.10 (per `pyproject.toml`)
- **Build System**: setuptools
- **Package Manager**: uv (confirmed via `uv.lock`)

## Project Structure

```
scietex.logging/
├── src/scietex/logging/
│   ├── __init__.py              # Public API exports
│   ├── _executor.py             # _WriteExecutor (single-thread executor helper)
│   ├── async_logging_handler.py # AsyncLoggingHandler machinery base
│   ├── config.py                # LoggingConfig, RedisConfig, ValkeyConfig, MqttConfig
│   ├── backend/
│   │   ├── console.py           # ConsoleBackend peer backend
│   │   └── file.py              # FileBackend peer backend
│   ├── formatter/
│   │   ├── scietex.py           # ScietexFormatter
│   │   └── json.py              # JsonFormatter (structured JSON output)
│   └── handler/
│       ├── console.py           # ConsoleHandler (console backend)
│       ├── file.py              # AsyncFileHandler + rotation variants
│       ├── broker.py            # AsyncBrokerHandler (base class for broker backends)
│       ├── redis.py             # AsyncRedisHandler (Redis backend)
│       ├── valkey.py            # AsyncValkeyHandler (Valkey backend)
│       └── mqtt.py              # AsyncMqttHandler (MQTT backend)
├── tests/
│   ├── test_async_logging_handler.py
│   ├── test_client_injection.py
│   ├── test_config.py
│   ├── test_console_backend.py
│   ├── test_console_handler.py
│   ├── test_executor.py
│   ├── test_file_backend.py
│   ├── test_file_handler.py
│   ├── test_formatter.py
│   ├── test_json_formatter.py
│   ├── test_message_broker_handler.py
│   ├── test_mqtt_handler.py
│   ├── test_queue_bounds.py
│   ├── test_redis_handler.py
│   ├── test_restartable_lifecycle.py
│   ├── test_valkey_handler.py
│   └── test_version.py
├── docs/
│   ├── architecture/
│   ├── index.md
│   ├── examples.md
│   ├── advanced.md
│   ├── backends.md
│   └── configuration.md
├── examples/
│   ├── all_backends.py
│   ├── basic_console_logging.py
│   ├── console_and_redis_logging.py
│   ├── custom_backend.py
│   ├── custom_formatter.py
│   ├── error_handler_and_queue_bounds.py
│   ├── file_logging.py
│   ├── injected_client.py
│   ├── mqtt_logging.py
│   ├── pure_machinery_handler.py
│   ├── redis_logging.py
│   ├── restartable_lifecycle.py
│   ├── valkey_logging.py
│   └── README.md
├── pyproject.toml               # Project configuration
├── uv.lock                      # Locked dependencies
└── README.md
```

## Public API

### Exported Classes (from `__init__.py`)

- `ConsoleHandler` - Console logging backend handler (always available)
- `AsyncBrokerHandler` - Base handler for message broker backends
- `AsyncFileHandler` - File logging backend (always available)
- `AsyncLoggingHandler` - Shared queue/worker machinery base for all handlers (always available)
- `AsyncRotatingFileHandler` - Size-based rotating file backend (always available)
- `AsyncTimedRotatingFileHandler` - Time-based rotating file backend (always available)
- `AsyncWatchedFileHandler` - Watched file backend (always available)
- `ConsoleBackend` - Console sink registered by `ConsoleHandler` (always available)
- `JsonFormatter` - Structured JSON output formatter (always available)
- `ScietexFormatter` - Custom formatter with 3-letter log level abbreviations and `%(name)s` logger-name identity
- `AsyncRedisHandler` - Redis logging backend (optional, requires `[redis]` extra)
- `AsyncValkeyHandler` - Valkey logging backend (optional, requires `[valkey]` extra)
- `AsyncMqttHandler` - MQTT logging backend (optional, requires `[mqtt]` extra)

### Exported Configuration Types (from `__init__.py`)

- `LoggingConfig` - Shared machinery options for every handler
- `RedisConfig` - Connection settings for the Redis backend
- `ValkeyConfig` - Connection settings for the Valkey backend
- `MqttConfig` - Connection settings for the MQTT backend

### Installation Extras

- `scietex.logging[redis]` - Install Redis support
- `scietex.logging[valkey]` - Install Valkey support
- `scietex.logging[mqtt]` - Install MQTT support (aiomqtt)
- `scietex.logging[all]` - Install all backends
- `scietex.logging[dev]` - Development dependencies (tox, redis, valkey, aiomqtt)
- `scietex.logging[lint]` - Linting (ruff, ty)
- `scietex.logging[test]` - Testing (pytest, pytest-asyncio)

## Architecture

### Handler Hierarchy

```
logging.Handler (standard library)
    └── AsyncLoggingHandler (src/scietex/logging/async_logging_handler.py)
        ├── ConsoleHandler (src/scietex/logging/handler/console.py)
        ├── AsyncFileHandler (src/scietex/logging/handler/file.py)
        │   ├── AsyncRotatingFileHandler
        │   ├── AsyncTimedRotatingFileHandler
        │   └── AsyncWatchedFileHandler
        └── AsyncBrokerHandler (src/scietex/logging/handler/broker.py)
            ├── AsyncRedisHandler (src/scietex/logging/handler/redis.py)
            ├── AsyncValkeyHandler (src/scietex/logging/handler/valkey.py)
            └── AsyncMqttHandler (src/scietex/logging/handler/mqtt.py)
```

### Key Concepts

1. **Asynchronous Queueing**: Log records are queued via `emit()` and processed by background workers
2. **Thread-safe `emit`**: `emit()` is safe to call from any thread (including one with no running asyncio loop); it writes to a thread-safe `queue.Queue` ingress and a bridge task re-dispatches records into the per-backend `asyncio.Queue`s. Off-loop `emit` now delivers instead of dropping.
3. **Event-Based Control**: 
   - `logging_accept_event` - Controls whether new logs are accepted
   - `logging_running_event` - Signals when logging workers are active
4. **Worker Pattern**: Each backend has its own queue and worker coroutine
5. **Graceful Shutdown**: `stop_logging()` waits for queues to drain with configurable timeout (default 5s)
6. **Client Injection**: Broker handlers accept an optional `client=` argument to use an externally-managed connection. When injected, the handler never calls `close()` on it — the caller owns the client's lifetime and recovery (`_owns_client`/`_injected_client`; `_connect()`/`_disconnect()` wrappers delegate to the abstract methods only when the handler owns the client).
7. **Async Write Offload**: Console and File backends format on the event-loop thread and run blocking `write`/`flush` (and rollover/reopen) on a per-backend single-thread executor (`_executor.py`'s `_WriteExecutor`). On teardown the file-owning worker submits its stream close to the *same* executor (serialized after any in-flight write) then `shutdown(wait=True)`, guaranteeing no write-after-close; the executor is worker-local, so handlers stay restartable.

### ScietexFormatter

- Identity comes from the standard-library logger name via `record.name`
  (set by `logging.getLogger("name")`), rendered by the `%(name)s` token
- Log levels abbreviated: `DBG`, `INF`, `WRN`, `ERR`, `CRT`
- Timestamps in ISO 8601 UTC format by default
- Signature: `ScietexFormatter(fmt=None, datefmt=None)`
- Default format: `%(asctime)s - %(levelname)s - [%(name)s] - %(message)s`

## Common Tasks

### Adding a New Backend

1. Create a new handler class inheriting from `AsyncBrokerHandler`
2. Implement `connect()`, `disconnect()`, and `send_message()` methods
3. Add optional import in `__init__.py` with try/except ImportError
4. Update `__all__` list in `__init__.py`
5. (Optional) Support client injection by accepting a `client=` keyword argument and forwarding it to `super().__init__()`; the base handles ownership.

### Running Examples

```bash
# Basic console logging
uv run python examples/basic_console_logging.py

# Redis logging (requires Redis running locally)
uv run python examples/redis_logging.py

# Valkey logging (requires Valkey running locally)
uv run python examples/valkey_logging.py

# Both console and Redis
uv run python examples/console_and_redis_logging.py

# Valkey logging with an externally-managed client (handler never closes it)
uv run python examples/injected_client.py

# MQTT logging (requires an MQTT broker running locally)
uv run python examples/mqtt_logging.py

# File logging (plain text + JSON)
uv run python examples/file_logging.py
```

### Running Tests

```bash
uv run pytest
```

### Running Linting

```bash
uv run ruff check .
```

## Important Notes

- **Console logging requires adding a `ConsoleHandler` to the logger explicitly**; file/broker handlers do not register a console sink.
- **Handlers must be started** with `await handler.start_logging()` before logging
- **Handlers must be stopped** with `await handler.stop_logging()` to ensure all logs are processed
- **Async context required**: `start_logging()` and `stop_logging()` are async and must be called within an asyncio event loop. `emit()` is thread-safe and does not need a loop — it may be called from any thread, including one with no running loop.
- **Formatter is console/file-specific**: Only `ConsoleHandler` and `AsyncFileHandler` (and its rotation variants) accept a `formatter=` keyword and render records through it (default `ScietexFormatter`). Broker handlers and the pure-machinery base `AsyncLoggingHandler` do not accept `formatter=` — broker wire payloads are built from the record directly.
- **Error handling**: Queue operations catch `queue.Full` (ingress overflow) and `asyncio.QueueFull` (backend overflow), plus other exceptions, and route them through the error channel to prevent crashes
- **Async write offload**: Since 1.6.0, an injected `file=` object's `write`/`flush` runs on a non-loop thread (the single-thread write executor), so an injected file-like must tolerate cross-thread writes. The handler still never closes an injected file-like.

## Known Issues & Gotchas

1. PostgreSQL support is mentioned in docs but not yet implemented (no `postgres` extra defined)
2. The `__init__.py` imports Redis/Valkey/MQTT handlers conditionally - ensure the `[redis]`, `[valkey]`, or `[mqtt]` extras are installed
3. `client` and a backend config (`valkey_config`/`redis_config`/`mqtt_config`/`backend_config`) are mutually exclusive — passing both raises `ValueError`. When injecting a client, omit the config dict.
4. `"_mqtt"` is a reserved queue name (used by `AsyncMqttHandler`); custom backends must not reuse it.
5. `"_file"` is a reserved queue name (used by `AsyncFileHandler`); custom backends must not reuse it.

## Development Commands

```bash
# Install in editable mode with all dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run linter
uv run ruff check .

# Format code (if using ruff format)
uv run ruff format .
```

## Related Files

- `/Users/anton/Projects/scietex.logging/README.md` - User-facing documentation
- `/Users/anton/Projects/scietex.logging/docs/index.md` - Detailed documentation
- `/Users/anton/Projects/scietex.logging/pyproject.toml` - Build configuration
- `/Users/anton/Projects/scietex.logging/examples/README.md` - Example documentation
