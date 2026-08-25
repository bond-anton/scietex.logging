# Examples

This document provides links to all example scripts in the `examples/` directory.

## Example Scripts

### Basic Console Logging

**File**: `examples/basic_console_logging.py`

Demonstrates asynchronous console logging using `AsyncBaseHandler`.

**Usage**:
```bash
uv run python examples/basic_console_logging.py
```

**Key Features**:
- Default console logging
- Multiple log levels
- Service and worker identification

---

### Redis Logging

**File**: `examples/redis_logging.py`

Demonstrates logging to a Redis stream using `AsyncRedisHandler`.

**Usage**:
```bash
uv run python examples/redis_logging.py
```

**Dependencies**:
- Redis must be running locally or configure remote connection

**Key Features**:
- Redis stream integration
- Service and worker identification
- Error and info logging

---

### Valkey Logging

**File**: `examples/valkey_logging.py`

Demonstrates logging to a Valkey stream using `AsyncValkeyHandler`.

**Usage**:
```bash
uv run python examples/valkey_logging.py
```

**Dependencies**:
- Valkey must be running locally or configure remote connection

**Key Features**:
- Valkey stream integration
- Service and worker identification
- Error and info logging

---

### Console and Redis Logging

**File**: `examples/console_and_redis_logging.py`

Demonstrates using both console and Redis logging simultaneously.

**Usage**:
```bash
uv run python examples/console_and_redis_logging.py
```

**Dependencies**:
- Redis must be running locally or configure remote connection

**Key Features**:
- Multiple handlers on same logger
- Console output (stdout)
- Redis stream logging

---

## Running Examples

All examples can be run with `uv`:

```bash
# Install dependencies
uv sync --all-extras

# Run any example
uv run python examples/example_name.py
```

## Example Code Structure

Each example follows this pattern:

1. Create logger and set level
2. Initialize handler(s)
3. Add handler(s) to logger
4. Start logging worker
5. Log messages
6. Stop logging worker

## Customizing Examples

Modify examples to explore features:

- Change service name and worker ID
- Adjust log levels
- Configure custom formatters
- Add additional backends
