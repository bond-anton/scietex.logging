# Roadmap

Planned direction for `scietex.logging`. Current stable release: **1.8.0**.

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

---

## 2.0 — Architecture-review follow-ups

**Status:** Proposed. Extracted from the deep architecture review
(`docs/reviews/architecture/2026-09-06.md`). These are the findings and open
questions that were **not** resolved in 1.0 because resolving them breaks the
frozen public API. Each is a candidate to bundle into the same 2.0 release as
the loop-independent `emit` work above.

### AR-116 — Constructor surface funnels subclass concerns

- **Severity:** LOW (design smell; HIGH confidence).
- **Problem:** `AsyncLoggingHandler.__init__` — the "pure machinery, no sink"
  base — accepts `stdout_enable` (console), `backend_config` (broker), and
  `formatter`, storing them on `config` without acting on them. Every subclass
  re-declares the full parameter list (eight handlers now repeat the signature:
  Redis, Valkey, MQTT, File, and the three rotation variants), so adding an
  option means touching all of them.
- **Recommendation:** a single `LoggingConfig`-accepting constructor (or
  narrower per-layer constructors) so options flow as data, not repeated kwargs.
- **Why 2.0:** changing the constructor signature across all eight handlers is a
  public-API breaking change. Deferred past 1.0 deliberately — locking the
  current surface at 1.0 first, then reshaping it in 2.0, is the correct semver
  posture.

### Open question 3 — Formatter scope on broker handlers

- **Problem:** `formatter=`/`setFormatter` affects console output only; broker
  payloads are built from config + record and are invariant under the formatter
  (AR-104, AR-018). The `formatter=` kwarg is threaded through every handler,
  so on a broker-only handler (`stdout_enable=False`) it is dead weight.
- **Options:** (a) move `formatter=` off the broker constructors so it is
  console-specific (breaking), or (b) add a broker payload-schema hook so a
  custom formatter can shape the wire record (feature).
- **Why 2.0:** option (a) is breaking; option (b) is a new public extension
  point. Either belongs in a 2.0 alongside AR-116 (which also reshapes the
  constructor surface).

### Open question 6 — Reserved backend names

- **Problem:** `queue_name="console"` collides with the console backend under
  the default `stdout_enable=True`, raising `ValueError` at construction
  (AR-109). `"console"`/`"redis"`/`"valkey"`/`"mqtt"`/`"file"` are de-facto
  reserved but not namespaced.
- **Options:** namespace the console backend (e.g. `"_console"`) so user
  `queue_name` values can never collide, or keep the loud `ValueError` and
  document the reserved names (the 1.0 choice).
- **Why 2.0:** namespacing changes the public `log_queues["console"]` key and
  the console drain-result name — breaking for any code that reads them.

### Remove deprecated `worker_id` (from the 1.x `instance_id` change)

- **Problem:** the 1.x `instance_id` change (see above) deprecates
  `worker_id: int` in favor of `instance_id: str`. The deprecated parameter
  must eventually be removed.
- **Why 2.0:** removing `worker_id` changes every handler constructor signature
  — a public-API breaking change. It is routed through 2.0 alongside AR-116
  (which also reshapes the constructor surface), so all constructor churn lands
  in one breaking release.
