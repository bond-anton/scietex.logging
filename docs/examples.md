# Examples

This document provides links to all example scripts in the `examples/` directory.
The examples progress from the happy path to advanced capabilities. Every example
follows the same lifecycle: create a logger, add a handler, start the logging
worker, log messages, then stop the worker.

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

### Custom Formatter

**File**: `examples/custom_formatter.py`

Demonstrates customizing `ScietexFormatter` with a custom `fmt` and `datefmt`,
then applying it to a handler with `setFormatter`.

**Usage**:
```bash
uv run python examples/custom_formatter.py
```

**Key Features**:
- Custom `fmt` and `datefmt` on `ScietexFormatter`
- `handler.setFormatter()` replaces the formatter for every sink

---

### Error Handler and Queue Bounds

**File**: `examples/error_handler_and_queue_bounds.py`

Demonstrates a bounded queue with drop-and-report overflow: a small
`queue_maxsize` combined with an `error_handler` callback that reports records
dropped when the queue is full.

**Usage**:
```bash
uv run python examples/error_handler_and_queue_bounds.py
```

**Key Features**:
- `queue_maxsize` bounds the backend queue
- `error_handler` callback reports dropped records
- Non-blocking `emit` under overload

---

### Custom Backend

**File**: `examples/custom_backend.py`

Demonstrates subclassing `AsyncBrokerHandler` into an in-memory backend that
appends records to a list, with no external service required.

**Usage**:
```bash
uv run python examples/custom_backend.py
```

**Key Features**:
- Subclass `AsyncBrokerHandler` and implement `connect`, `disconnect`, `send_message`
- `stdout_enable=False` drops the inherited console backend
- Broker-only handler with no external service

---

### Pure Machinery Handler

**File**: `examples/pure_machinery_handler.py`

Demonstrates subclassing `AsyncLoggingHandler` directly (no console sink) and
registering a custom backend with `register_backend`.

**Usage**:
```bash
uv run python examples/pure_machinery_handler.py
```

**Key Features**:
- Subclass `AsyncLoggingHandler` directly
- `register_backend` adds a custom queue/worker/drain backend
- No console sink

---

### Restartable Lifecycle

**File**: `examples/restartable_lifecycle.py`

Demonstrates the handler lifecycle in depth: start/stop cycles, idempotent
stop, double-start `RuntimeError`, and `stop_logging(timeout)`.

**Usage**:
```bash
uv run python examples/restartable_lifecycle.py
```

**Key Features**:
- Start/stop/restart cycles
- Idempotent `stop_logging`
- Double-start raises `RuntimeError`
- `stop_logging(timeout)` drain timeout

---

### All Backends

**File**: `examples/all_backends.py`

Demonstrates console, Redis, and Valkey backends on a single logger
simultaneously, with explicit `redis_config`/`valkey_config` and
`stdout_enable=False` on the broker handlers.

**Usage**:
```bash
uv run python examples/all_backends.py
```

**Dependencies**:
- Redis and Valkey must be running locally or configure remote connection

**Key Features**:
- Console, Redis, and Valkey on one logger
- Explicit `redis_config` and `valkey_config`
- `stdout_enable=False` on broker handlers to avoid duplicate console output

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
- Configure custom formatters (`custom_formatter.py`)
- Add an `error_handler` callback and tune `queue_maxsize`
  (`error_handler_and_queue_bounds.py`)
- Build a custom backend by subclassing `AsyncBrokerHandler`
  (`custom_backend.py`) or `AsyncLoggingHandler` directly
  (`pure_machinery_handler.py`)
- Restart a handler across start/stop cycles (`restartable_lifecycle.py`)
- Add additional backends
