# Lifecycle

This document covers the runtime lifecycle of a handler instance: construction,
start, normal operation, shutdown, and resource ownership. All lifecycle methods
are async and must run inside an asyncio event loop.

## Construction (`__init__`)

**`AsyncLoggingHandler.__init__`** (`async_logging_handler.py:111`):
- Constructs a `ScietexFormatter(service_name, worker_id)`.
- Creates two `asyncio.Event`s: `logging_accept_event`, `logging_running_event`
  (both initially **unset**).
- Initializes empty `log_queues`, `log_worker_factories`, `_drain_hooks`,
  `_status_reporters`, `log_workers_tasks`.

**`AsyncBaseHandler.__init__`** (`basic_handler.py:33`): calls super, sets
`stdout_enable` (default True). If `stdout_enable`: constructs a
`ConsoleBackend` and registers it under the name `"console"` via
`register_backend` (queue + worker factory + drain hook), and registers the
console's `report_status` as a status reporter via `register_status_reporter`.

**`AsyncBrokerHandler.__init__`** (`message_broker_handler.py:41`): calls super,
then registers `log_queues[queue_name]` and `self._worker` via
`register_backend`. Sets `client = None` by default; when an external `client`
is injected it stores it durably (`_injected_client`) and marks the handler as
non-owning (`_owns_client = False`). Passing both `client` and `backend_config`
raises `ValueError`.

**`AsyncRedisHandler.__init__`** / **`AsyncValkeyHandler.__init__`**: call super
with `queue_name="redis"` / `"valkey"` and `backend_config` (a typed
`RedisConfig` / `ValkeyConfig`), then store `stream_name`. `client_config` is a
derived read-only `asdict` view of `backend_config`, not a stored raw dict. No
connection is opened at construction.

**State after construction.** Events unset; queues empty; worker factories
registered (not yet invoked); no client connection. The handler is inert until
`start_logging()`.

## Startup (`start_logging`)

`AsyncLoggingHandler.start_logging` (`async_logging_handler.py:234`):
1. `logging_accept_event.set()` — `emit` may now enqueue.
2. `logging_running_event.set()` — workers may run.
3. `log_workers_tasks = [asyncio.create_task(factory()) for factory in
   log_worker_factories]` — each worker factory is invoked to produce a fresh
   coroutine, which becomes a scheduled task.

For broker handlers, the broker worker begins by calling `connect()`
(`message_broker_handler.py:92`), which lazily opens the client connection
(Redis `redis.Redis(...)`; Valkey `GlideClient.create(...)`). The console
worker needs no connection.

**Ownership note.** `start_logging` does not create new workers; it invokes the
registered worker factories to schedule fresh tasks each cycle. Calling
`start_logging` while already running raises `RuntimeError` (see the guard
semantics below).

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

`AsyncLoggingHandler.stop_logging(timeout=5.0)` (`async_logging_handler.py:301`)
— see data-flow.md Flow 4 for the full sequence. Summary:
1. Stop accepting new records (`accept_event.clear()`).
2. Drain every registered backend **concurrently** under one shared timeout while
   the workers are still running: `stop_logging` schedules every `drain(timeout)`
   hook via `asyncio.gather`, which preserves registration order in the collected
   results. After all drains conclude, invoke each registered status reporter
   with the collected results — the console backend is registered as a status
   reporter and enqueues synthetic records describing how every backend fared.
   The coordinator owns result collection, so no backend depends on drain
   registration order.
3. Signal workers to stop (`running_event.clear()`).
4. Gather worker tasks, bounded by the same `timeout` used for the drains
   (`asyncio.wait_for`). A worker blocked inside `connect()`/`send_message()`
   cannot observe the running-event clear until the call returns, so on
   `asyncio.TimeoutError` `stop_logging` cancels the straggler tasks and
   re-gathers with `return_exceptions=True` rather than hanging graceful
   shutdown on an unreachable broker. Broker workers run `disconnect()` in a
   `finally`, so the client is released on both normal exit and cancellation.
5. Reset `log_workers_tasks = []` so a later stop does not re-gather finished
   tasks.
6. Clear any records still queued after the drain window and worker teardown:
   each backend queue is drained via `get_nowait()` + `task_done()`
   (`async_logging_handler.py:387-395`). Undelivered records are **dropped, not
   replayed**, so the next `start_logging` begins from an actually-empty queue
   (AR-020). `stop_logging` does **not** call `self.close()`.

## Restartable lifecycle

A handler is **restartable**: `start_logging()` and `stop_logging()` may be
called repeatedly on the same event loop, giving multiple start/stop cycles.
Because workers are stored as *factories* (zero-argument callables returning a
fresh coroutine), each `start_logging` schedules fresh tasks. Any record still
queued when `stop_logging` finishes is dropped (see Shutdown step 6), so each
start begins from an actually-empty queue rather than re-scheduling consumed
coroutines or replaying undelivered records.

**Guard semantics.**

| Call | When | Behavior |
|---|---|---|
| `start_logging()` | already running | raises `RuntimeError` |
| `start_logging()` | after `close()` | raises `RuntimeError` (a closed handler cannot be restarted) |
| `stop_logging()` | never started / already stopped | no-op (idempotent) |
| `stop_logging()` | after `close()` | still runs (not blocked by `_closed`) |
| `close()` | any | marks closed, clears accept event; workers continue until `await stop_logging()` |
| `emit(record)` | between stop and next start | silent no-op (records dropped by design; accept event unset) |
| `emit(record)` | off event-loop thread | drop + report via error channel (never raises) |
| restart | same event loop | supported |
| restart | different event loop | unsupported — events/queues are loop-bound; reconstruct the handler |

**Loop binding.** The events and queues are bound to the event loop they were
created on. Restart is only supported on the same loop; a handler cannot be
moved across loops. To log on a different loop, construct a fresh handler.

## Cleanup & resource ownership

| Resource | Created | Owned by | Released |
|---|---|---|---|
| `asyncio.Event`s | `__init__` | handler instance | cleared in `stop_logging` |
| `asyncio.Queue`s | backend `__init__` (console/broker) | handler instance | drained in `stop_logging`; not explicitly closed |
| worker factories | backend `__init__` | handler instance | invoked in `start_logging`; tasks gathered in `stop_logging` |
| client connection (`client`) | `connect()` (worker start) | handler instance | `disconnect()` (worker exit) |
| formatter | `__init__` | handler instance | — |

The `client connection` row above holds only for a **self-managed** client (one
built by the handler's own `connect()`). An **injected** client is owned by the
caller, not the handler, and is never closed by `disconnect()`.

**Ownership model.** All async resources are instance-scoped and owned by the
handler. There is no global state and no shared resource across handler
instances. The one exception is an **injected client**: when a caller passes
`client=`, the caller owns its lifetime and recovery — the handler never closes
it. The host application is responsible for calling `start_logging` /
`stop_logging` in the correct order and within an event loop.

### Cleanup via stdlib `logging.shutdown()`

At interpreter exit, stdlib `logging.shutdown()` calls `flush()` then `close()`
on every handler. Those hooks are synchronous and cannot `await`, so the async
teardown contract (`await stop_logging()`) cannot run there. The overrides
reconcile the two contracts:

- `close()` marks the handler closed (a later `start_logging()` raises
  `RuntimeError`) and clears the accept event. It does **not** stop the workers
  or close a broker client — graceful teardown still requires
  `await stop_logging()`, which is **not** blocked by `_closed`.
- `flush()` is a documented no-op: records are flushed by the async workers
  during `stop_logging()`, which cannot be awaited from the synchronous hook.
- `close()` does not call `stop_logging()`, and `stop_logging()` does not call
  `close()`. They are separate terminal operations: `close()` is the stdlib's
  synchronous terminal; `stop_logging()` is the async graceful-drain terminal.

## Background tasks / workers

- **Worker tasks** (`log_workers_tasks`) are the long-lived background
  consumers, one per backend queue. Created in `start_logging`, terminated in
  `stop_logging`.

## Notable lifecycle observations (facts, not judgments)

- Worker factories are registered once in `__init__` and invoked by
  `start_logging` to schedule fresh tasks on every start cycle.
- `register_backend` raises `ValueError` if a backend name is already registered
  (`async_logging_handler.py:231-232`), so a duplicate name cannot silently
  overwrite a queue while doubling the worker and drain hooks (AR-028).
- The broker client connection is opened lazily by the worker's `connect()`
  and closed by `disconnect()` at worker exit — connection lifetime is tied to
  worker lifetime, not to `start_logging`/`stop_logging` directly.
- `stop_logging` has a default 5s timeout shared across all backend drains
  (which run concurrently), plus the same timeout for the worker gather; a slow
  backend delays shutdown up to that single shared budget rather than
  multiplying it per backend.
- Backend queues are **bounded** by `queue_maxsize` (default 10000) and are
  reused across restart cycles (they are created in `__init__`, not per start).
  A queue that is still full when logging restarts drops new records by policy:
  `emit` reports each `asyncio.QueueFull` through the error channel rather than
  blocking. Restart does not change this contract — a full queue always drops +
  reports, never blocks.
