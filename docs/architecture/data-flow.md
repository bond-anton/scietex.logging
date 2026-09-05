# Data Flow

## Flow 1: Console logging (default backend)

**Source.** Host application calls `logger.info(...)` (or any level) on a
`logging.Logger` that has an `AsyncBaseHandler` attached.

**Processing components.**
1. `logging` framework → `AsyncLoggingHandler.emit(record)`
   (`async_logging_handler.py:259`, inherited by `AsyncBaseHandler`).
2. `emit` calls `queue.put_nowait(record)` on the `"console"` queue registered
   by `AsyncBaseHandler`.
3. `ConsoleBackend._worker` (`console_backend.py:86`) gets the record from the
   queue.
4. `ScietexFormatter.format(record)` (`formatter.py:90`) decorates the record
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
1. `logging` framework → `AsyncLoggingHandler.emit(record)` (inherited).
2. `emit` calls `queue.put_nowait(record)` on the broker queue (`"redis"` /
   `"valkey"`), registered by `AsyncBrokerHandler.__init__`.
3. `AsyncBrokerHandler._worker` (`message_broker_handler.py:139`) gets the
   record, calls `connect()` on first entry, and builds a **dict** log entry:
   `{"level": level_abbreviation(record.levelno), "message": record.getMessage(),
   "name": f"{self.config.service_name}:{self.config.worker_id}",
   "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat()}`.
   `level` is computed via `level_abbreviation(record.levelno)`; `name` and
   `time` are derived from `self.config` and the record directly, not from the
   formatter.
4. `send_message(log_entry)` dispatches to the concrete backend:
   - Redis: `client.xadd(stream_name, record)` (`redis_handler.py:110`).
   - Valkey: `client.xadd(stream_name, record.items())` (`valkey_handler.py:111`).
5. On worker exit, `disconnect()` closes the client.

**Destination.** Redis stream / Valkey stream (external server).

**Transformations.** `LogRecord` → dict with keys `level`, `message`, `name`,
`time`. The dict fields are computed **independently** of the formatter: `level`
is derived from `record.levelno` via `level_abbreviation` (e.g. `"INF"`), and
`name`/`time` from `self.config` (the `service_name:worker_id` identity) and the
record's `created` timestamp. The broker wire format is therefore invariant
under `setFormatter` and deterministic regardless of `stdout_enable`.

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
(`async_logging_handler.py:301`).

**Processing.**
1. `logging_accept_event.clear()` — stops `emit` from enqueuing new records.
2. For each registered backend's `drain(timeout)` hook, in **registration
   order** (while the workers are still running): the hook waits for its queue
   to drain and returns a `BackendDrainResult` describing the outcome. The
   coordinator collects the results.
3. After every drain concludes, invoke each registered status reporter with the
   collected results. The console backend is registered as a status reporter
   (`basic_handler.py:88`), so `ConsoleBackend.report_status(results)`
   (`console_backend.py:130`) enqueues synthetic INFO/ERROR status records for
   every backend's drain outcome.
4. `logging_running_event.clear()` — signals workers to stop after draining.
5. `await asyncio.gather(*log_workers_tasks)` — workers exit.
6. `log_workers_tasks = []` — forget finished tasks so a later stop does not
   re-gather them. `stop_logging` does **not** call `close()`; the handler may
   be restarted via `start_logging` on the same loop.

**Destination.** All queues drained; workers terminated; handler idle and
restartable.

## Cross-cutting notes

- **Single record, multiple queues.** `emit` fans one `LogRecord` out to every
  registered queue in `log_queues` (`async_logging_handler.py:259`). Queue count
  = 1 (console) for `AsyncBaseHandler`, or 2 (console + broker) for broker
  handlers with stdout enabled.
- **Bounded queues with drop + report overflow.** Each backend queue is bounded
  by `queue_maxsize` (default 10000): `ConsoleBackend` builds
  `asyncio.Queue(maxsize=maxsize)` and `AsyncBrokerHandler` builds
  `asyncio.Queue(maxsize=self.queue_maxsize)`. When a queue is full at emit
  time, `emit` drops the record and routes an `asyncio.QueueFull` to the error
  channel (`_report_error` → `error_handler` callback or module logger). `emit`
  never blocks, so under sustained overload records drop + report rather than
  buffering unboundedly.
- **Ordering.** Within a single queue, records are FIFO. Across queues (console
  vs broker) there is no ordering guarantee.
- **Synthetic records during shutdown.** `ConsoleBackend.report_status`
  (`console_backend.py:130`) injects status `LogRecord`s into the console queue
  to report every backend's drain results — a control-flow message traveling on
  the same data path as user logs. It runs as a post-drain status reporter, so
  it observes all backends' outcomes without depending on drain order.
