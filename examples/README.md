# Examples for `scietex.logging`

This directory contains runnable example scripts demonstrating how to use the
`scietex.logging` package for asynchronous logging in Python applications. The
examples progress from the happy path (basic console logging) to advanced
capabilities (custom formatters, bounded queues with overflow reporting, custom
backends, restartable lifecycles, and multiple backends on one logger). Every
example follows the same lifecycle: create a logger, add a handler, start the
logging worker, log messages, then stop the worker.

## Prerequisites

Install the base package plus the extras required by the examples you want to
run. The table maps each example to the installation it needs.

| Installation | Examples |
| --- | --- |
| Base package only | `basic_console_logging.py`, `custom_formatter.py`, `error_handler_and_queue_bounds.py`, `custom_backend.py`, `pure_machinery_handler.py`, `restartable_lifecycle.py` |
| `scietex.logging[redis]` | `redis_logging.py`, `console_and_redis_logging.py`, `all_backends.py` |
| `scietex.logging[valkey]` | `valkey_logging.py`, `all_backends.py` |
| `scietex.logging[all]` (or `uv sync --all-extras`) | everything |

## Example Index

| Example | What it teaches | Needs a server |
| --- | --- | --- |
| [basic_console_logging.py](./basic_console_logging.py) | Minimal console logging with `AsyncBaseHandler` | No |
| [redis_logging.py](./redis_logging.py) | Log to a Redis stream with `AsyncRedisHandler` | Redis |
| [valkey_logging.py](./valkey_logging.py) | Log to a Valkey stream with `AsyncValkeyHandler` | Valkey |
| [console_and_redis_logging.py](./console_and_redis_logging.py) | Console and Redis handlers on one logger | Redis |
| [custom_formatter.py](./custom_formatter.py) | Customize `ScietexFormatter` and apply it with `setFormatter` | No |
| [error_handler_and_queue_bounds.py](./error_handler_and_queue_bounds.py) | `error_handler` callback and `queue_maxsize` drop-and-report overflow | No |
| [custom_backend.py](./custom_backend.py) | Subclass `AsyncBrokerHandler` into an in-memory backend (`stdout_enable=False`) | No |
| [pure_machinery_handler.py](./pure_machinery_handler.py) | Subclass `AsyncLoggingHandler` directly and register a backend | No |
| [restartable_lifecycle.py](./restartable_lifecycle.py) | Start/stop cycles, idempotent stop, double-start `RuntimeError`, `stop_logging(timeout)` | No |
| [all_backends.py](./all_backends.py) | Console, Redis, and Valkey simultaneously with explicit configs | Redis + Valkey |

## Running the Examples

Run any example with `uv`:

```commandline
uv run python examples/<name>.py
```

Replace `<name>` with the example file you want to run. The examples that need
a server (`redis_logging.py`, `valkey_logging.py`, `console_and_redis_logging.py`,
`all_backends.py`) assume Redis and/or Valkey are running locally on the default
host and port. To point at a remote host, edit the `redis_config` dict (Redis)
or the `valkey_config` `GlideClientConfiguration` (Valkey) inside the script
before running.

## Lifecycle

All examples share the same pattern:

1. Create a logger and set its level.
2. Initialize one or more handlers.
3. Add the handler(s) to the logger.
4. `await handler.start_logging()` to start the background worker(s).
5. Log messages.
6. `await handler.stop_logging()` to drain and stop the worker(s).

`restartable_lifecycle.py` explores this lifecycle in depth: `stop_logging` is
idempotent, starting while already running raises `RuntimeError`, and each
`start_logging` schedules fresh worker tasks from a clean queue.
