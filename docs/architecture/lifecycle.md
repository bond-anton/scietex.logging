# Lifecycle

This document covers the runtime lifecycle of a handler instance: construction,
start, normal operation, shutdown, and resource ownership. All lifecycle methods
are async and must run inside an asyncio event loop.

## Construction (`__init__`)

**`AsyncBaseHandler.__init__`** (`basic_handler.py:56`):
- Sets `stdout_enable` (default True) and `_queue_put_cleanup_threshold`
  (default 100, clamped to >= 1).
- Constructs a `ScietexFormatter(service_name, worker_id)`.
- Creates two `asyncio.Event`s: `logging_accept_event`, `logging_running_event`
  (both initially **unset**).
- If `stdout_enable`: creates `log_queues["console"]` and appends
  `self._console_logging_worker()` (a coroutine, not yet a task) to
  `log_workers`.
- Initializes empty `log_workers_tasks`, `log_queue_put_tasks`.

**`AsyncBrokerHandler.__init__`** (`message_broker_handler.py:33`): calls super,
then adds `log_queues[queue_name]` and appends `self._worker()` to
`log_workers`. Sets `client = None`.

**`AsyncRedisHandler.__init__`** / **`AsyncValkeyHandler.__init__`**: call super
with `queue_name="redis"` / `"valkey"`, store `stream_name` and `client_config`.
No connection is opened at construction.

**State after construction.** Events unset; queues empty; workers are coroutine
objects (not running); no client connection. The handler is inert until
`start_logging()`.

## Startup (`start_logging`)

`AsyncBaseHandler.start_logging` (`basic_handler.py:100`):
1. `logging_accept_event.set()` — `emit` may now enqueue.
2. `logging_running_event.set()` — workers may run.
3. `log_workers_tasks = [asyncio.create_task(w) for w in log_workers]` — each
   worker coroutine becomes a scheduled task.

For broker handlers, the broker worker begins by calling `connect()`
(`message_broker_handler.py:104`), which lazily opens the client connection
(Redis `redis.Redis(...)`; Valkey `GlideClient.create(...)`). The console
worker needs no connection.

**Ownership note.** `start_logging` does not create new workers; it schedules
the coroutines created in `__init__`. Calling `start_logging` twice would
re-schedule the same (already-consumed) coroutines — behavior is
`UNKNOWN`/undefined for repeated start without stop.

## Normal operation

- **Produce:** host logs → `emit(record)` → schedules `queue.put` tasks
  (tracked in `log_queue_put_tasks`, pruned at threshold).
- **Consume:** each worker loops `while running_event.is_set() or not
  queue.empty()`, doing `await asyncio.wait_for(queue.get(), 1)`. A 1-second
  timeout on `get()` lets the loop re-check the running event even when idle.
- **Console worker** formats and writes to stdout.
- **Broker worker** builds a dict and calls `send_message` (network I/O).
- **Connection stays open** for the worker's lifetime (opened in `connect` at
  worker start, closed in `disconnect` at worker exit).

## Shutdown (`stop_logging`)

`AsyncBaseHandler.stop_logging(timeout=5.0)` (`basic_handler.py:152`) — see
data-flow.md Flow 4 for the full sequence. Summary:
1. Stop accepting new records (`accept_event.clear()`).
2. Drain in-flight put tasks.
3. Signal workers to stop (`running_event.clear()`).
4. Wait (timeout-bounded) for each non-console queue to join; report status via
   synthetic console records.
5. Drain console queue.
6. Gather worker tasks (workers exit their loop and, for broker workers, call
   `disconnect()`).
7. `self.close()`.

**Idempotency / re-start.** After `stop_logging`, the events are cleared and
workers have exited. Whether the handler can be cleanly restarted via a second
`start_logging()` is `UNKNOWN` — the worker coroutines were consumed on first
start and are not recreated by `start_logging`.

## Cleanup & resource ownership

| Resource | Created | Owned by | Released |
|---|---|---|---|
| `asyncio.Event`s | `__init__` | handler instance | cleared in `stop_logging` |
| `asyncio.Queue`s | `__init__` | handler instance | drained in `stop_logging`; not explicitly closed |
| worker coroutines | `__init__` | handler instance | scheduled in `start_logging`, gathered in `stop_logging` |
| `queue.put` tasks | `emit` | handler instance (`log_queue_put_tasks`) | pruned periodically + drained in `stop_logging` |
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
- **Put tasks** (`log_queue_put_tasks`) are short-lived per-record tasks
  created by `emit`; they complete quickly and are pruned/drained.

## Notable lifecycle observations (facts, not judgments)

- Worker coroutines are created once in `__init__` and scheduled in
  `start_logging`; they are not recreated on restart.
- The broker client connection is opened lazily by the worker's `connect()`
  and closed by `disconnect()` at worker exit — connection lifetime is tied to
  worker lifetime, not to `start_logging`/`stop_logging` directly.
- `stop_logging` has a default 5s timeout per queue drain; a slow backend could
  cause the console drain or worker gather to wait up to that timeout.
