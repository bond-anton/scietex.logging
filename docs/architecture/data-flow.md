# Data Flow

## Flow 1: Console logging (default backend)

**Source.** Host application calls `logger.info(...)` (or any level) on a
`logging.Logger` that has an `AsyncBaseHandler` attached.

**Processing components.**
1. `logging` framework → `AsyncLoggingHandler.emit(record)`
   (`async_logging_handler.py:341`, inherited by `AsyncBaseHandler`).
2. `emit` writes the record to the shared thread-safe `_ingress` queue
   (`queue.Queue`, bounded by `queue_maxsize`), then wakes the bridge task.
3. The bridge task (`_bridge_loop`, `async_logging_handler.py:395`) moves the
   record from `_ingress` into the `"console"` queue registered by
   `AsyncBaseHandler`.
4. `ConsoleBackend._worker` (`console_backend.py:86`) gets the record from the
   queue.
5. `ScietexFormatter.format(record)` (`formatter.py:90`) decorates the record
   (sets `worker_name`, abbreviates `levelname`) and renders the text.

**Destination.** `sys.stdout` (via `sys.stdout.write(... + "\n")` and flush).

**Transformations.** `LogRecord` → formatted string. Level int → 3-letter
abbreviation; timestamp → ISO-8601 UTC; `worker_name` injected.

**Async boundary.** A thread-safe stdlib `queue.Queue` ingress between `emit`
(sync producer, any thread) and the bridge task, then the `asyncio.Queue`
between the bridge and the console worker (async consumer). `emit` never
blocks on I/O.

## Flow 2: Broker logging (Redis / Valkey)

**Source.** Host application log call on a logger with an
`AsyncRedisHandler` / `AsyncValkeyHandler` attached.

**Processing components.**
1. `logging` framework → `AsyncLoggingHandler.emit(record)` (inherited).
2. `emit` writes the record to the shared thread-safe `_ingress`; the bridge
   task moves it into the broker queue (`"redis"` / `"valkey"`), registered by
   `AsyncBrokerHandler.__init__`.
3. `AsyncBrokerHandler._worker` (`message_broker_handler.py:153`) gets the
   record, calls `connect()` on first entry, and builds a **dict** log entry:
   `{"level": level_abbreviation(record.levelno), "message": record.getMessage(),
   "name": f"{self.config.service_name}:{self.config.worker_id}",
   "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat()}`.
   `level` is computed via `level_abbreviation(record.levelno)` (imported from
   `config.py`); `name` and `time` are derived from `self.config` and the record
   directly, not from the formatter.
4. `send_message(log_entry)` dispatches to the concrete backend:
   - Redis: `client.xadd(stream_name, record)` (`redis_handler.py:119`).
   - Valkey: `client.xadd(stream_name, record.items())` (`valkey_handler.py:125`).
   Each raises `RuntimeError` when the client is not connected (AR-034).
5. On worker exit, `disconnect()` closes the client. A failed `connect()` is
   retried with capped exponential backoff (0.5s base doubling to a 30s cap with
   ±20% jitter, `message_broker_handler.py:20-22,164-180`) — AR-022.

**Destination.** Redis stream / Valkey stream (external server).

**Transformations.** `LogRecord` → dict with keys `level`, `message`, `name`,
`time`. The dict fields are computed **independently** of the formatter: `level`
is derived from `record.levelno` via `level_abbreviation` (e.g. `"INF"`), and
`name`/`time` from `self.config` (the `service_name:worker_id` identity) and the
record's `created` timestamp. The broker wire format is therefore invariant
under `setFormatter` and deterministic regardless of `stdout_enable`.

**Async boundary.** Thread-safe `_ingress` between `emit` and the bridge, then
the `asyncio.Queue` between the bridge and the broker worker; the worker's
network I/O (`xadd`) is awaited inside the worker coroutine, so it does not
block `emit` or the host application.

## Flow 3: Console + broker combined (per-handler)

A broker handler with `stdout_enable=True` runs **both** a console worker and
a broker worker. `emit` writes the same `LogRecord` once into the shared
`_ingress`; the bridge task fans it out into every queue in `log_queues`
(console + broker). The record is therefore consumed twice: once by the console
worker (formatted to stdout) and once by the broker worker (converted to a dict
and sent to the stream). See `examples/console_and_redis_logging.py` for the
two-handler variant.

## Flow 4: Shutdown / drain (control flow)

**Source.** Host application calls `await handler.stop_logging(timeout=5.0)`
(`async_logging_handler.py:433`).

**Processing.**
1. `logging_accept_event.clear()` — stops `emit` from enqueuing new records.
2. Stop the bridge task and flush the ingress **before** the backend drains run:
   the bridge is cancelled, then the same `get_nowait` → `put_nowait` fan-out
   runs inline until `_ingress` is empty, moving every buffered record into the
   per-backend queues. This guarantees no record still in the ingress is lost
   when the drains complete and the workers stop.
3. Schedule every registered backend's `drain(timeout)` hook **concurrently**
   via `asyncio.gather` under one shared timeout (while the workers are still
   running): each hook waits for its queue to drain and returns a
   `BackendDrainResult` describing the outcome. `gather` preserves registration
   order, so the coordinator collects the results in the order the backends were
   registered.
4. After every drain concludes, invoke each registered status reporter with the
   collected results. The console backend is registered as a status reporter
   (`basic_handler.py:94`), so `ConsoleBackend.report_status(results)`
   (`console_backend.py:183`) enqueues synthetic INFO/ERROR status records for
   every backend's drain outcome.
5. `logging_running_event.clear()` — signals workers to stop after draining.
6. `await asyncio.gather(*log_workers_tasks)` — workers exit (bounded by the
   drain `timeout`; stragglers are cancelled on `asyncio.TimeoutError`).
7. `log_workers_tasks = []` — forget finished tasks so a later stop does not
   re-gather them.
8. Clear any records still queued after the drain window and worker teardown:
   each backend queue is drained via `get_nowait()` + `task_done()`, and any
   leftover ingress entry (an in-flight `emit` that raced teardown) is cleared
   (`async_logging_handler.py:534-542`). Undelivered records are **dropped, not
   replayed**, so the next `start_logging` begins from an actually-empty queue
   (AR-020). `stop_logging` does **not** call `close()`; the handler may be
   restarted via `start_logging` on the same loop. `close()` is a separate
   terminal operation for stdlib `logging.shutdown()`: it marks the handler
   closed (refusing a later `start_logging()`) but does not stop workers or
   close a broker client, and it does not call `stop_logging()`.

**Destination.** All queues drained (leftovers cleared); workers terminated;
handler idle and restartable.

## Cross-cutting notes

- **Single record, multiple queues.** `emit` writes one `LogRecord` into the
  shared `_ingress` once; the bridge task fans it out to every registered queue
  in `log_queues` (`async_logging_handler.py:395`). Queue count = 1 (console)
  for `AsyncBaseHandler`, or 2 (console + broker) for broker handlers with
  stdout enabled.
- **Bounded queues with drop + report overflow.** The shared `_ingress` and
  each backend queue are bounded by `queue_maxsize` (default 10000):
  `ConsoleBackend` builds `asyncio.Queue(maxsize=maxsize)` and
  `AsyncBrokerHandler` builds `asyncio.Queue(maxsize=self.queue_maxsize)`. When
  the ingress is full at emit time, `emit` drops the record and routes a
  `queue.Full` to the error channel (`_report_error` → `error_handler` callback
  or module logger); if a backend queue is full when the bridge re-dispatches,
  the bridge reports an `asyncio.QueueFull` instead. `emit` never blocks, so
  under sustained overload records drop + report rather than buffering
  unboundedly. Worst-case buffering is `(1+N) × queue_maxsize` (the shared
  ingress plus the N backend queues).
- **Ordering.** Within a single queue, records are FIFO. Across queues (console
  vs broker) there is no ordering guarantee.
- **Synthetic records during shutdown.** `ConsoleBackend.report_status`
  (`console_backend.py:183`) injects status `LogRecord`s into the console queue
  to report every backend's drain results — a control-flow message traveling on
  the same data path as user logs. It runs as a post-drain status reporter, so
  it observes all backends' outcomes without depending on drain order.
