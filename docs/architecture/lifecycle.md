# Lifecycle

This document covers the runtime lifecycle of a handler instance: construction,
start, normal operation, shutdown, and resource ownership. All lifecycle methods
are async and must run inside an asyncio event loop.

## Construction (`__init__`)

**`AsyncLoggingHandler.__init__`** (`async_logging_handler.py:88`):
- Constructs a `ScietexFormatter(service_name, worker_id)`.
- Creates two `asyncio.Event`s: `logging_accept_event`, `logging_running_event`
  (both initially **unset**).
- Initializes empty `log_queues`, `log_workers`, `_drain_hooks`,
  `log_workers_tasks`.

**`AsyncBaseHandler.__init__`** (`basic_handler.py:31`): calls super, sets
`stdout_enable` (default True). If `stdout_enable`: constructs a
`ConsoleBackend` and registers it under the name `"console"` via
`register_backend` (queue + worker coroutine + drain hook).

**`AsyncBrokerHandler.__init__`** (`message_broker_handler.py:38`): calls super,
then registers `log_queues[queue_name]` and `self._worker()` via
`register_backend`. Sets `client = None`.

**`AsyncRedisHandler.__init__`** / **`AsyncValkeyHandler.__init__`**: call super
with `queue_name="redis"` / `"valkey"`, store `stream_name` and `client_config`.
No connection is opened at construction.

**State after construction.** Events unset; queues empty; workers are coroutine
objects (not running); no client connection. The handler is inert until
`start_logging()`.

## Startup (`start_logging`)

`AsyncLoggingHandler.start_logging` (`async_logging_handler.py:156`):
1. `logging_accept_event.set()` — `emit` may now enqueue.
2. `logging_running_event.set()` — workers may run.
3. `log_workers_tasks = [asyncio.create_task(w) for w in log_workers]` — each
   worker coroutine becomes a scheduled task.

For broker handlers, the broker worker begins by calling `connect()`
(`message_broker_handler.py:116`), which lazily opens the client connection
(Redis `redis.Redis(...)`; Valkey `GlideClient.create(...)`). The console
worker needs no connection.

**Ownership note.** `start_logging` does not create new workers; it schedules
the coroutines created in `__init__`. Calling `start_logging` twice would
re-schedule the same (already-consumed) coroutines — behavior is
`UNKNOWN`/undefined for repeated start without stop.

## Normal operation

- **Produce:** host logs → `emit(record)` → `queue.put_nowait(record)` on each
  registered backend queue.
- **Consume:** each worker loops `while running_event.is_set() or not
  queue.empty()`, doing `await asyncio.wait_for(queue.get(), 1)`. A 1-second
  timeout on `get()` lets the loop re-check the running event even when idle.
- **Console worker** (`ConsoleBackend._worker`) formats and writes to stdout.
- **Broker worker** builds a dict and calls `send_message` (network I/O).
- **Connection stays open** for the worker's lifetime (opened in `connect` at
  worker start, closed in `disconnect` at worker exit).

## Shutdown (`stop_logging`)

`AsyncLoggingHandler.stop_logging(timeout=5.0)` (`async_logging_handler.py:209`)
— see data-flow.md Flow 4 for the full sequence. Summary:
1. Stop accepting new records (`accept_event.clear()`).
2. Signal workers to stop (`running_event.clear()`).
3. Drain every registered backend through its `drain(timeout, results)` hook in
   reverse registration order; the console backend (registered first) drains
   last and reports the other backends' outcomes via synthetic records.
4. Gather worker tasks (workers exit their loop and, for broker workers, call
   `disconnect()`).
5. `self.close()`.

**Idempotency / re-start.** After `stop_logging`, the events are cleared and
workers have exited. Whether the handler can be cleanly restarted via a second
`start_logging()` is `UNKNOWN` — the worker coroutines were consumed on first
start and are not recreated by `start_logging`.

## Cleanup & resource ownership

| Resource | Created | Owned by | Released |
|---|---|---|---|
| `asyncio.Event`s | `__init__` | handler instance | cleared in `stop_logging` |
| `asyncio.Queue`s | backend `__init__` (console/broker) | handler instance | drained in `stop_logging`; not explicitly closed |
| worker coroutines | backend `__init__` | handler instance | scheduled in `start_logging`, gathered in `stop_logging` |
| client connection (`client`) | `connect()` (worker start) | handler instance | `disconnect()` (worker exit) |
| formatter | `__init__` | handler instance | — |

**Ownership model.** All async resources are instance-scoped and owned by the
handler. There is no global state and no shared resource across handler
instances. The host application is responsible for calling `start_logging` /
`stop_logging` in the correct order and within an event loop.

## Background tasks / workers

- **Worker tasks** (`log_workers_tasks`) are the long-lived background
  consumers, one per backend queue. Created in `start_logging`, terminated in
  `stop_logging`.

## Notable lifecycle observations (facts, not judgments)

- Worker coroutines are created once in `__init__` and scheduled in
  `start_logging`; they are not recreated on restart.
- The broker client connection is opened lazily by the worker's `connect()`
  and closed by `disconnect()` at worker exit — connection lifetime is tied to
  worker lifetime, not to `start_logging`/`stop_logging` directly.
- `stop_logging` has a default 5s timeout per backend drain; a slow backend
  could cause the console drain or worker gather to wait up to that timeout.
