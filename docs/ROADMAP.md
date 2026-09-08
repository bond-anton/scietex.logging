# Roadmap

Planned direction for `scietex.logging`. In-development version: **2.0.0**
(not yet released).

The 1.x public API (`__all__` surface and constructor signatures) has grown
additively through the 1.x line (client injection, MQTT backend, file sinks,
`JsonFormatter`) without breaking the surface. Anything that **breaks** the
existing API is routed through a **2.0** release.

## 1.x — Loop-independent (thread-safe) `emit`

**Status:** Implemented in 1.7.0.

`emit()` is now thread-safe: it writes each record to a shared stdlib
`queue.Queue` ingress (bounded by `queue_maxsize`) and a bridge asyncio task on
the event-loop thread re-dispatches records into the per-backend
`asyncio.Queue`s. Off-loop `emit` — from a worker thread or a thread with no
running loop — now **delivers** the record instead of dropping it and reporting
it through the error channel (AR-102 resolved).

This is a **behavior change, not an API break**: `emit(self, record)` keeps its
stdlib `logging.Handler` signature, and no constructor, `__all__` entry, or
`start_logging()`/`stop_logging()` contract changed. Off-loop `emit` was
previously dropped-and-reported as a documented limitation; it now delivers,
which is strictly more permissive — a fix of that limitation rather than a
contract break. It therefore shipped as a **1.x** minor release (1.7.0),
documented as a behavior change in the changelog.

### Problem (resolved in 1.7.0)

`emit()` was bound to the event-loop thread. It enqueued directly into
per-backend `asyncio.Queue` objects that the workers consume on the loop thread,
and `asyncio.Queue.put_nowait` is not thread-safe. `emit()` therefore compared
`asyncio.get_running_loop()` against the loop captured at `start_logging()` and,
when called off-loop (e.g. from a worker thread or thread-pool executor — common
in asyncio applications), dropped the record and reported it through the error
channel (AR-102). The logger was loop-agnostic (it binds to whatever loop is
running at `start_logging`) but not thread-safe: `emit` had to run on that
loop's thread. In 1.7.0 the off-loop guard was replaced by a thread-safe
ingress + bridge task, so `emit` is now safe from any thread.

### Goal

Make `emit()` safe to call from **any thread**, including with no running loop,
so the logger works in the standard asyncio + threadpool pattern.

### Design

Decouple the producer from the asyncio machinery with a thread-safe buffer:

1. **Thread-safe producer queue.** Front each backend's `asyncio.Queue` with a
   stdlib `queue.SimpleQueue`/`queue.Queue` that `emit()` writes to from any
   thread. `emit()` becomes a pure synchronous, thread-safe `put` — the off-loop
   guard (AR-102) is removed.
2. **Bridge task on the loop thread.** A single asyncio task polls the
   thread-safe buffer (e.g. `loop.run_in_executor` on a blocking `get`, a
   `loop.call_soon_threadsafe` wakeup, or periodic polling) and re-dispatches
   each record into the per-backend `asyncio.Queue`s. Standard
   thread-safe-ingress → loop-bound-fan-out pattern.
3. **`start_logging()` still captures the loop**, but only the bridge needs it;
   `emit()` no longer does.
4. **Shutdown coordination.** `stop_logging()` must drain the thread-safe buffer
   as well as the asyncio queues, or buffered records are lost. The drain
   contract extends to the bridge.

### Trade-offs to accept

- **Ordering.** A thread-safe buffer + bridge can reorder records across
  threads. Strict global ordering requires serializing producers (a lock),
  reintroducing contention.
- **Latency.** The bridge adds a hop; records sit in the buffer until it polls.
  Trade the current immediate `put_nowait` for a small batching delay.
- **Backpressure.** A blocking `put` would let a slow consumer block the producer
  thread; use a bounded non-blocking put with the existing drop-and-report
  overflow policy.
- **Workers stay loop-bound.** Only the ingress needs to be thread-safe; the
  console/broker workers remain internal loop tasks.

### Out of scope (separate concern)

Cross-loop restart (running the whole handler on a different loop than the one
that started it) is a smaller, distinct issue. `_loop` is re-captured each
`start_logging()`, so restarting on a new loop already works once the old
workers are fully stopped; the only gap is `emit` during the transition
comparing against a stale loop, which the thread-safe buffer also resolves.

### Related findings

- AR-102 (off-loop `emit` drops-and-reports) — the guard this design removes.
- AR-113 (`_loop` reset on stop) — already done in 1.0 (P2).

---

## 1.x — `instance_id` replaces `worker_id`

**Status:** Implemented in 1.8.0.

The handler identity is now a string `instance_id: str | None = None` that
defaults to `"1"` and is rendered into the formatter's `worker_name` as
`service_name:instance_id`. The numeric `worker_id: int | None = None` parameter
is deprecated (a `DeprecationWarning` is emitted when it is used) and stringified
into `instance_id`; supplying both raises `ValueError`.

### Change

1. Add `instance_id: str | None = None` as a keyword parameter next to
   `worker_id` in `AsyncLoggingHandler.__init__` (and, by inheritance, every
   handler that forwards it).
2. Internally switch from `worker_id` to `instance_id`, which defaults to
   `"1"` when neither is supplied.
3. Accept **both** for now for backward compatibility, but raise `ValueError`
   if both are provided at the same time (they are aliases for the same
   concept; supplying both is contradictory).
4. Mark `worker_id: int` as **deprecated** (documented + `DeprecationWarning`),
   to be removed in v2.0.

### Why 1.x

This is an **additive, backward-compatible** change: `worker_id` keeps working
unchanged, and `instance_id` is a new optional parameter. No existing
constructor signature, `__all__` entry, or method contract breaks. It therefore
belongs in a **1.x** minor release (e.g. 1.8.0), with the deprecation warning
giving users a migration window before the v2.0 removal.

### Why 2.0 (removal)

Removing the deprecated `worker_id` parameter entirely is a **public-API
breaking** change (it changes every handler constructor signature), so it is
routed through the **2.0** release alongside the other constructor-surface
changes (AR-116).

> **Historical note (2.0.0):** this section is retained as history. In 2.0.0 the
> entire identity surface — `service_name`, `worker_id`, `instance_id`, the
> `worker_name` property, and the `resolve_instance_id` helper — was removed in
> favor of the standard-library logger name (`record.name`). See the
> "Remove identity parameters" item under the 2.0 section below.

---

## 2.0 — Architecture-review follow-ups

**Status:** Proposed. Extracted from the deep architecture review
(`docs/reviews/architecture/2026-09-06.md`). These are the findings and open
questions that were **not** resolved in 1.0 because resolving them breaks the
frozen public API. Each is a candidate to bundle into the same 2.0 release as
the loop-independent `emit` work above.

### AR-116 — Constructor surface funnels subclass concerns (partially resolved in 2.0.0)

- **Severity:** LOW (design smell; HIGH confidence).
- **Original problem:** the pure-machinery base `AsyncLoggingHandler.__init__`
  accepted `stdout_enable`, `backend_config`, and `formatter`, storing them on
  `config` without acting on them, and every subclass re-declared the full
  parameter list (eight handlers repeated the signature: Redis, Valkey, MQTT,
  File, and the three rotation variants), so adding an option meant touching
  all of them.
- **Resolved in 2.0.0:** `stdout_enable` removed everywhere; `formatter` removed
  from the base and all broker handlers, now owned by `ConsoleHandler` and
  `AsyncFileHandler` (and its rotation variants); identity params removed;
  console decoupled into a dedicated `ConsoleHandler`. Layer-specific params
  (`client`, file params, broker dict configs) are now correctly scoped to
  their layer.
- **Residual (resolved in 2.0.0):** `backend_config` was still accepted by the
  base (`async_logging_handler.py:145`) and stored on `config` without the base
  reading it — the only readers were the broker subclasses. In 2.0.0 it was
  removed from the base constructor and from `LoggingConfig`; broker handlers
  now store the typed config as their own `backend_config` attribute (AR-004).
  The remaining forwarding boilerplate is the re-declared
  `error_handler`/`queue_maxsize` across the concrete constructors (boilerplate,
  not a layer leak).
- **Decision:** a single `LoggingConfig`-accepting constructor was evaluated and
  rejected — it moves the boilerplate without removing it and degrades
  ergonomics for the common `ConsoleHandler()` case. The base's `backend_config`
  seam was removed in 2.0.0 (broker handlers own their config). **No further
  work required.**

### Open question 3 — Formatter scope on broker handlers (resolved in 2.0.0)

- **Problem:** `formatter=`/`setFormatter` affects console output only; broker
  payloads are built from config + record and are invariant under the formatter
  (AR-104, AR-018). The `formatter=` kwarg was threaded through every handler,
  so on a broker handler it was dead weight — the broker has no console sink to
  render it (the console sink now lives in `ConsoleHandler`).
- **Resolution (2.0.0):** option (a) — `formatter=` was removed from the
  pure-machinery base `AsyncLoggingHandler` and from all broker handlers
  (`AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`,
  `AsyncMqttHandler`). `ConsoleHandler` and `AsyncFileHandler` (and its rotation
  variants) now own their own `formatter`, defaulting to `ScietexFormatter`.
  Passing `formatter=` to a broker handler or the base now raises `TypeError`.

### Open question 6 — Reserved backend names

- **Problem:** `queue_name="console"` collides with the console backend
  registered by `ConsoleHandler`, raising `ValueError` at construction
  (AR-109). `"console"`/`"redis"`/`"valkey"`/`"mqtt"`/`"file"` are de-facto
  reserved but not namespaced.
- **Options:** namespace the console backend (e.g. `"_console"`) so user
  `queue_name` values can never collide, or keep the loud `ValueError` and
  document the reserved names (the 1.0 choice).
- **Why 2.0:** namespacing changes the public `log_queues["console"]` key and
  the console drain-result name — breaking for any code that reads them.
- **Resolution (2.0.0):** option (a) — namespacing — was chosen. The built-in
  queue keys are namespaced to `"_console"` / `"_file"` / `"_redis"` /
  `"_valkey"` / `"_mqtt"`, the `_` prefix is reserved for internal use, and the
  shutdown status text is unchanged.

### Remove identity parameters — implemented in 2.0.0

- **Problem:** the 1.x line carried `service_name`, the deprecated `worker_id`,
  and `instance_id` as the handler identity. All three — plus the `worker_name`
  property and the `resolve_instance_id` helper — are removed in 2.0.0 in favor
  of the standard-library logger name. Identity now comes solely from
  `record.name` (set by `logging.getLogger("name")`), rendered via `%(name)s` in
  the default `ScietexFormatter` format and emitted as the `name`/`logger` field
  by broker/JSON sinks.
- **Why 2.0:** removing these parameters changes every handler constructor and
  the `ScietexFormatter` signature — a hard public-API breaking change. It lands
  in 2.0.0 alongside AR-116 (which also reshapes the constructor surface), so
  all constructor churn lands in one breaking release.
