# Data Flow

## Flow 1: Console logging (default backend)

**Source.** Host application calls `logger.info(...)` (or any level) on a
`logging.Logger` that has an `AsyncBaseHandler` attached.

**Processing components.**
1. `logging` framework → `AsyncBaseHandler.emit(record)` (`basic_handler.py:115`).
2. `emit` schedules `asyncio.create_task(queue.put(record))` on the `"console"`
   queue and tracks the task in `log_queue_put_tasks`.
3. `AsyncBaseHandler._console_logging_worker` (`basic_handler.py:243`) gets the
   record from the queue.
4. `ScietexFormatter.format(record)` (`formatter.py:89`) decorates the record
   (sets `worker_name`, abbreviates `levelname`) and renders the text.

**Destination.** `sys.stdout` (via `sys.stdout.write(... + "\n")` and flush).

**Transformations.** `LogRecord` → formatted string. Level int → 3-letter
abbreviation; timestamp → ISO-8601 UTC; `worker_name` injected.

**Async boundary.** `asyncio.Queue` between `emit` (sync producer) and the
console worker (async consumer). `emit` never blocks on I/O.

## Flow 2: Broker logging (Redis / Valkey)

**Source.** Host application log call on a logger with an
`AsyncRedisHandler` / `AsyncValkeyHandler` attached.

**Processing components.**
1. `logging` framework → `AsyncBaseHandler.emit(record)` (inherited).
2. `emit` schedules `queue.put(record)` on the broker queue (`"redis"` /
   `"valkey"`), added by `AsyncBrokerHandler.__init__`.
3. `AsyncBrokerHandler._worker` (`message_broker_handler.py:93`) gets the
   record, calls `connect()` on first entry, and builds a **dict** log entry:
   `{"level": record.levelname, "message": record.getMessage(),
   "name": logger_name, "time": formatter.formatTime(record)}`.
   `logger_name` = `record.worker_name` if present else `record.name`.
4. `send_message(log_entry)` dispatches to the concrete backend:
   - Redis: `client.xadd(stream_name, record)` (`redis_handler.py:102`).
   - Valkey: `client.xadd(stream_name, record.items())` (`valkey_handler.py:105`).
5. On worker exit, `disconnect()` closes the client.

**Destination.** Redis stream / Valkey stream (external server).

**Transformations.** `LogRecord` → dict with keys `level`, `message`, `name`,
`time`. Note the dict is built from the **already-decorated** record: `level`
is the abbreviated string (e.g. `"INF"`), `name` is `service_name:worker_id`.

**Async boundary.** `asyncio.Queue` between `emit` and the broker worker; the
worker's network I/O (`xadd`) is awaited inside the worker coroutine, so it
does not block `emit` or the host application.

## Flow 3: Console + broker combined (per-handler)

A broker handler with `stdout_enable=True` runs **both** a console worker and
a broker worker. `emit` enqueues the same `LogRecord` into every queue in
`log_queues` (console + broker). The record is therefore consumed twice: once
by the console worker (formatted to stdout) and once by the broker worker
(converted to a dict and sent to the stream). See
`examples/console_and_redis_logging.py` for the two-handler variant.

## Flow 4: Shutdown / drain (control flow)

**Source.** Host application calls `await handler.stop_logging(timeout=5.0)`
(`basic_handler.py:152`).

**Processing.**
1. `logging_accept_event.clear()` — stops `emit` from enqueuing new records.
2. `await asyncio.gather(*log_queue_put_tasks)` — waits for in-flight
   `queue.put` tasks to land.
3. `logging_running_event.clear()` — signals workers to stop after draining.
4. For each **non-console** queue: `await asyncio.wait_for(queue.join(),
   timeout)`. On success/timeout/error, if `stdout_enable`, a synthetic
   INFO/ERROR `LogRecord` is enqueued to the console queue describing the
   outcome (`basic_handler.py:186-222`).
5. Console queue drained last: `await asyncio.wait_for(console.join(), timeout)`.
6. `await asyncio.gather(*log_workers_tasks)` — workers exit.
7. `self.close()` — stdlib `logging.Handler.close()`.

**Destination.** All queues drained; workers terminated; handler closed.

## Cross-cutting notes

- **Single record, multiple queues.** `emit` fans one `LogRecord` out to every
  queue in `log_queues` (`basic_handler.py:133`). Queue count = 1 (console) for
  `AsyncBaseHandler`, or 2 (console + broker) for broker handlers with stdout
  enabled.
- **No backpressure / bounded queues.** Queues are unbounded `asyncio.Queue()`
  (no `maxsize`). `QueueFull` is caught in `emit` but can never be raised by an
  unbounded queue.
- **Ordering.** Within a single queue, records are FIFO. Across queues (console
  vs broker) there is no ordering guarantee.
- **Synthetic records during shutdown.** `stop_logging` injects status
  `LogRecord`s into the console queue to report broker-queue drain results —
  a control-flow message traveling on the same data path as user logs.
