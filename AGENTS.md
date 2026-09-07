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
│   ├── async_logging_handler.py # AsyncLoggingHandler machinery base
│   ├── basic_handler.py         # AsyncBaseHandler (base class with console backend)
│   ├── config.py                # LoggingConfig, RedisConfig, ValkeyConfig, MqttConfig
│   ├── console_backend.py       # ConsoleBackend peer backend
│   ├── file_backend.py          # FileBackend peer backend
│   ├── file_handler.py          # AsyncFileHandler + rotation variants
│   ├── formatter.py             # ScietexFormatter
│   ├── json_formatter.py        # JsonFormatter (structured JSON output)
│   ├── message_broker_handler.py # AsyncBrokerHandler (base class for broker backends)
│   ├── mqtt_handler.py          # AsyncMqttHandler (MQTT backend)
│   ├── redis_handler.py         # AsyncRedisHandler (Redis backend)
│   └── valkey_handler.py        # AsyncValkeyHandler (Valkey backend)
├── tests/
│   ├── test_async_logging_handler.py
│   ├── test_basic_handler.py
│   ├── test_config.py
│   ├── test_console_backend.py
│   ├── test_client_injection.py
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

- `AsyncBaseHandler` - Base handler with console logging backend (always available)
- `AsyncBrokerHandler` - Base handler for message broker backends
- `AsyncFileHandler` - File logging backend (always available)
- `AsyncLoggingHandler` - Shared queue/worker machinery base for all handlers (always available)
- `AsyncRotatingFileHandler` - Size-based rotating file backend (always available)
- `AsyncTimedRotatingFileHandler` - Time-based rotating file backend (always available)
- `AsyncWatchedFileHandler` - Watched file backend (always available)
- `ConsoleBackend` - Console sink registered by `AsyncBaseHandler` (always available)
- `JsonFormatter` - Structured JSON output formatter (always available)
- `ScietexFormatter` - Custom formatter with worker name and 3-letter log level abbreviations
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
        └── AsyncBaseHandler (src/scietex/logging/basic_handler.py)
            ├── AsyncFileHandler (src/scietex/logging/file_handler.py)
            │   ├── AsyncRotatingFileHandler
            │   ├── AsyncTimedRotatingFileHandler
            │   └── AsyncWatchedFileHandler
            └── AsyncBrokerHandler (src/scietex/logging/message_broker_handler.py)
                ├── AsyncRedisHandler (src/scietex/logging/redis_handler.py)
                ├── AsyncValkeyHandler (src/scietex/logging/valkey_handler.py)
                └── AsyncMqttHandler (src/scietex/logging/mqtt_handler.py)
```

### Key Concepts

1. **Asynchronous Queueing**: Log records are queued via `emit()` and processed by background workers
2. **Event-Based Control**: 
   - `logging_accept_event` - Controls whether new logs are accepted
   - `logging_running_event` - Signals when logging workers are active
3. **Worker Pattern**: Each backend has its own queue and worker coroutine
4. **Graceful Shutdown**: `stop_logging()` waits for queues to drain with configurable timeout (default 5s)
5. **Client Injection**: Broker handlers accept an optional `client=` argument to use an externally-managed connection. When injected, the handler never calls `close()` on it — the caller owns the client's lifetime and recovery (`_owns_client`/`_injected_client`; `_connect()`/`_disconnect()` wrappers delegate to the abstract methods only when the handler owns the client).

### ScietexFormatter

- Service name and worker ID included in logs: `{service_name}:{worker_id}`
- Log levels abbreviated: `DBG`, `INF`, `WRN`, `ERR`, `CRT`
- Timestamps in ISO 8601 UTC format by default
- Default format: `%(asctime)s - %(levelname)s - [%(worker_name)s] - %(message)s`

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

- **Console logging is always enabled** by default in `AsyncBaseHandler` (controlled by `stdout_enable` parameter)
- **Handlers must be started** with `await handler.start_logging()` before logging
- **Handlers must be stopped** with `await handler.stop_logging()` to ensure all logs are processed
- **Async context required**: All worker methods are async and must be called within an asyncio event loop
- **Backends share the same formatter**: All handlers use the configured formatter
- **Error handling**: Queue operations catch `QueueFull`, `InvalidStateError`, and other exceptions to prevent crashes

## Known Issues & Gotchas

1. PostgreSQL support is mentioned in docs but not yet implemented (no `postgres` extra defined)
2. The `__init__.py` imports Redis/Valkey/MQTT handlers conditionally - ensure the `[redis]`, `[valkey]`, or `[mqtt]` extras are installed
3. `client` and a backend config (`valkey_config`/`redis_config`/`mqtt_config`/`backend_config`) are mutually exclusive — passing both raises `ValueError`. When injecting a client, omit the config dict.
4. `"mqtt"` is a reserved queue name (used by `AsyncMqttHandler`); custom backends must not reuse it.
5. `"file"` is a reserved queue name (used by `AsyncFileHandler`); custom backends must not reuse it.

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
