# AGENTS.md

This file provides guidance for OpenCode agents working on the `scietex.logging` repository.

## Project Overview

`scietex.logging` is an asynchronous Python logging package that provides non-blocking logging capabilities. It supports multiple backends including console logging and Redis logging (with Valkey support coming soon).

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
│   ├── basic_handler.py         # AsyncBaseHandler (base class with console backend)
│   ├── formatter.py             # ScietexFormatter and level_abbreviation helper
│   ├── message_broker_handler.py # AsyncBrokerHandler (base class for broker backends)
│   ├── redis_handler.py         # AsyncRedisHandler (Redis backend)
│   └── valkey_handler.py        # AsyncValkeyHandler (Valkey backend)
├── examples/
│   ├── basic_console_logging.py
│   ├── redis_logging.py
│   ├── console_and_redis_logging.py
│   └── README.md
├── pyproject.toml               # Project configuration
├── uv.lock                      # Locked dependencies
└── README.md
```

## Public API

### Exported Classes (from `__init__.py`)

- `AsyncBaseHandler` - Base handler with console logging backend (always available)
- `AsyncBrokerHandler` - Base handler for message broker backends
- `ScietexFormatter` - Custom formatter with worker name and 3-letter log level abbreviations
- `AsyncRedisHandler` - Redis logging backend (optional, requires `[redis]` extra)
- `AsyncValkeyHandler` - Valkey logging backend (optional, requires `[valkey]` extra)

### Installation Extras

- `scietex.logging[redis]` - Install Redis support
- `scietex.logging[valkey]` - Install Valkey support  
- `scietex.logging[all]` - Install all backends
- `scietex.logging[dev]` - Development dependencies (tox, redis, valkey)
- `scietex.logging[lint]` - Linting (ruff)
- `scietex.logging[test]` - Testing (pytest, pytest-asyncio)

## Architecture

### Handler Hierarchy

```
logging.Handler (standard library)
    └── AsyncBaseHandler (src/scietex/logging/basic_handler.py)
        └── AsyncBrokerHandler (src/scietex/logging/message_broker_handler.py)
            ├── AsyncRedisHandler (src/scietex/logging/redis_handler.py)
            └── AsyncValkeyHandler (src/scietex/logging/valkey_handler.py)
```

### Key Concepts

1. **Asynchronous Queueing**: Log records are queued via `emit()` and processed by background workers
2. **Event-Based Control**: 
   - `logging_accept_event` - Controls whether new logs are accepted
   - `logging_running_event` - Signals when logging workers are active
3. **Worker Pattern**: Each backend has its own queue and worker coroutine
4. **Graceful Shutdown**: `stop_logging()` waits for queues to drain with configurable timeout (default 5s)

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

### Running Examples

```bash
# Basic console logging
uv run python examples/basic_console_logging.py

# Redis logging (requires Redis running locally)
uv run python examples/redis_logging.py

# Both console and Redis
uv run python examples/console_and_redis_logging.py
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
2. The `__init__.py` imports Redis/Valkey handlers conditionally - ensure the `[redis]` or `[valkey]` extras are installed

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

- `/home/anton/Projects/scietex.logging/README.md` - User-facing documentation
- `/home/anton/Projects/scietex.logging/pyproject.toml` - Build configuration
- `/home/anton/Projects/scietex.logging/examples/README.md` - Example documentation (note: contains package name inconsistencies)
