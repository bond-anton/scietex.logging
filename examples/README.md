# Examples for `scietex.logging`

This directory contains example scripts demonstrating how to use the `scietex.logging` package
for asynchronous logging in Python applications. These examples illustrate the use of console,
Redis, and Valkey logging backends provided by `AsyncBaseHandler`, `AsyncRedisHandler`, and `AsyncValkeyHandler`.

## Table of Contents

- [Getting Started](#getting-started)
- [Example Scripts](#example-scripts)
  - [1. Basic Console Logging](#1-basic-console-logging)
  - [2. Redis Logging](#2-redis-logging)
  - [3. Valkey Logging](#3-valkey-logging)
  - [4. Logging to Both Console and Redis](#4-logging-to-both-console-and-redis)

---

## Getting Started

Ensure that the `scietex.logging` package and any additional dependencies are installed. To install with optional Redis support:

```commandline
pip install scietex.logging[redis]
```

Or install all backends:

```commandline
pip install scietex.logging[all]
```

To run each example, use the following command:
```commandline
python examples/example_script_name.py
```
Replace `example_script_name.py` with the name of the example you wish to run.

## Example Scripts

### 1. Basic Console Logging
File: [basic_console_logging.py](./basic_console_logging.py)

This example demonstrates how to set up asynchronous console logging using `AsyncBaseHandler`.
The logs will be printed to the standard output, making this setup suitable for simple applications or debugging.

Usage:
```commandline
python examples/basic_console_logging.py
```

---

### 2. Redis Logging
File: [redis_logging.py](./redis_logging.py)

This example demonstrates how to log messages to a Redis stream using `AsyncRedisHandler`.
The Redis backend allows log messages to be stored in a Redis stream,
providing persistence and support for high-throughput applications.

Dependencies: Make sure Redis is running locally or specify the connection details in `redis_config` within the script.

Usage:
```commandline
python examples/redis_logging.py
```

---

### 3. Valkey Logging
File: [valkey_logging.py](./valkey_logging.py)

This example demonstrates how to log messages to a Valkey stream using `AsyncValkeyHandler`.
The Valkey backend allows log messages to be stored in a Valkey stream,
providing persistence and support for high-throughput applications.

Dependencies: Make sure Valkey is running locally or specify the connection details in `valkey_config` within the script.

Usage:
```commandline
python examples/valkey_logging.py
```

---

### 4. Logging to Both Console and Redis
File: [console_and_redis_logging.py](./console_and_redis_logging.py)

This example demonstrates how to configure a logger with multiple handlers by using
both `AsyncBaseHandler` and `AsyncRedisHandler`. Although `AsyncRedisHandler` already
includes console logging inherited from `AsyncBaseHandler`, this example shows how to add
separate logging handlers to the same logger, a common scenario in complex applications
that require logging to multiple destinations simultaneously.

Usage:
```commandline
python examples/console_and_redis_logging.py
```

---

## Notes

  - Ensure that scietex.logging and any required dependencies (like Redis or Valkey) are properly installed.
  - For Valkey support, install with: `pip install scietex.logging[valkey]`
  - These examples can serve as starting points for integrating scietex.logging into your own projects.

Feel free to modify the examples to explore additional features of scietex.logging and tailor
the logging configuration to your application's needs.
