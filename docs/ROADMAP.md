# Roadmap

Planned direction for `scietex.logging`. Current stable release: **1.6.0**.

The 1.x public API (`__all__` surface and constructor signatures) has grown
additively through the 1.x line (client injection, MQTT backend, file sinks,
`JsonFormatter`) without breaking the surface. Anything that **breaks** the
existing API is routed through a **2.0** release.

## 2.0 — Loop-independent (thread-safe) `emit`

**Status:** Proposed (design only; not yet implemented).

### Problem

`emit()` is bound to the event-loop thread. It enqueues directly into
per-backend `asyncio.Queue` objects that the workers consume on the loop thread,
and `asyncio.Queue.put_nowait` is not thread-safe. `emit()` therefore compares
`asyncio.get_running_loop()` against the loop captured at `start_logging()` and,
when called off-loop (e.g. from a worker thread or thread-pool executor — common
in asyncio applications), drops the record and reports it through the error
channel (AR-102). The logger is loop-agnostic (it binds to whatever loop is
running at `start_logging`) but not thread-safe: `emit` must run on that loop's
thread.

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

---

## 1.x — Async redesign of Console and File backends

**Status:** Implemented in 1.6.0.

The Console and File backends previously performed **synchronous, blocking I/O
inside their worker coroutines**: `ConsoleBackend._worker` called
`sys.stdout.write` + `flush`, and `FileBackend._worker` / `AsyncFileHandler._worker`
called `stream.write` + `flush` directly. Under heavy log volume or a slow sink
(a piped stdout, a network filesystem, a slow disk), this blocked the event loop
thread for the duration of each write, stalling every other coroutine on the
loop.

In 1.6.0 the blocking write/flush/rollover/reopen now runs on a **dedicated
single-thread executor per backend/handler** (`src/scietex/logging/_executor.py`),
so the worker coroutine awaits the write instead of blocking the loop.
Formatting stays on the event-loop thread; only the blocking I/O moves off it.
On teardown the file-owning workers submit their stream close to the same
executor and call `shutdown(wait=True)`, guaranteeing **no write-after-close**
even when a write outlives the stop timeout — a deliberate
correctness-over-latency trade-off (see `docs/configuration.md`).

This is a **behavior change, not an API break**: no public signature, `__all__`
surface, constructor, or method contract changed. It shipped as a 1.x minor
release (1.6.0) and is documented as a behavior change in the changelog.
