# Changelog

All notable changes to `scietex.logging` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2026-09-07

### Changed

- **Loop-independent (thread-safe) `emit`**: `emit()` can now be called from any
  thread, including a thread with no running event loop. Previously, an off-loop
  `emit` dropped the record and reported it through the error channel; now it
  delivers. This is a behavior change, not an API break: `emit(self, record)`
  keeps its stdlib signature and no constructor, `__all__` surface, or
  `start_logging()`/`stop_logging()` contract changes.
- **Implementation**: a shared bounded thread-safe ingress queue
  (stdlib `queue.Queue`) plus a bridge asyncio task that re-dispatches records
  into the per-backend queues. Formatting still happens on the event-loop thread
  in each worker; the bridge only moves records.
- **Overflow nuance**: when the ingress is full, `emit` reports `queue.Full`
  (previously `asyncio.QueueFull` at the backend queue). Per-backend overflow
  still reports `asyncio.QueueFull`.
- **Resource note**: worst-case buffering is now `(1 + N) × queue_maxsize` (the
  shared ingress plus the N backend queues).

## [1.6.0] - 2026-09-07

### Changed

- **Async redesign of Console and File backends**: the blocking `write`/`flush`
  (and, for the rotation variants, rollover/reopen) that the Console and File
  worker coroutines performed directly on the event-loop thread now run on a
  dedicated single-thread executor per backend/handler, so a slow sink (piped
  stdout, a network filesystem, a slow disk) no longer stalls the event loop.
  Formatting still happens on the event-loop thread; only the blocking I/O moves
  off it. This is a behavior change, not an API break: no public signature,
  `__all__` surface, constructor, or method contract changes.
- **Injected `file=` objects now receive writes off the loop thread**: when an
  externally-managed file-like is injected via `file=`, its `write`/`flush` are
  now called from a background worker thread rather than the event-loop thread.
  The handler still never closes an injected file-like (the caller owns its
  lifetime). Code that injects a file-like whose `write`/`flush` are not
  thread-safe should account for this.
- **`stop_logging` waits for in-flight writes**: on shutdown the file-owning
  workers submit their stream close to the same single-thread executor
  (`shutdown(wait=True)`), so the handler waits for any in-flight `write` to
  complete before closing the stream — no write-after-close — even when the
  write outlives the stop timeout.

## [1.5.0] - 2026-09-07

### Features

- **File sinks**: new `AsyncFileHandler` writes log records to a file, mirroring
  the standard-library `logging.FileHandler` signature (`filename`, `mode`,
  `encoding`, `delay`, `errors`) plus the scietex options. Rotation variants
  `AsyncRotatingFileHandler`, `AsyncTimedRotatingFileHandler`, and
  `AsyncWatchedFileHandler` reuse the stdlib rollover logic (driven from the
  background worker, never from `emit`). Supports the `file=` injection seam for
  an externally-managed, already-open file-like the handler never closes.
- **`JsonFormatter`**: a `logging.Formatter` subclass that renders each record
  as a single-line JSON object (`timestamp`, `level`, `logger`, `message`,
  optional `exception`, plus flattened user `extra` fields), suitable for
  newline-delimited JSON (NDJSON) file sinks.
- **File example**: `examples/file_logging.py` demonstrates plain-text and JSON
  file output.

## [1.4.0] - 2026-09-07

### Docs

- **ReadTheDocs documentation**: added a full Sphinx + MyST-Parser build (`docs/conf.py`, `docs/requirements.txt`, `.readthedocs.yaml`) with an autodoc API reference, a toctree over the existing Markdown guides, and a `docs` CI job running `sphinx-build -W` (warnings-as-errors). Internal directories (`architecture/`, `reviews/`, `superpowers/`, `ROADMAP.md`) are excluded from the user-facing build.

## [1.3.0] - 2026-09-07

### Features

- **MQTT backend**: new `AsyncMqttHandler` publishes log records as JSON to an MQTT topic via `aiomqtt`. Configure with `mqtt_config` (host, port, username, password, identifier, keepalive, clean_session, transport, timeout, tls_insecure) and `qos`/`retain` publish flags. Requires the `[mqtt]` extra (`aiomqtt~=2.5.0`). Supports the `client=` injection seam (the injected client must already be connected).
- **`MqttConfig`**: typed connection settings for the MQTT backend; the `backend_config` union now includes it.
- **MQTT example**: `examples/mqtt_logging.py` demonstrates publishing logs to an MQTT broker.

## [1.2.0] - 2026-09-07

### Features

- **Optional client-injection seam**: inject an externally-managed broker client into `AsyncBrokerHandler`, `AsyncRedisHandler`, or `AsyncValkeyHandler` via the new `client=` keyword argument. When a client is injected the handler never closes it — the caller owns its lifetime and recovery. Passing both `client` and a backend config raises `ValueError`.
- **Injected-client example**: `examples/injected_client.py` demonstrates injecting an app-owned Valkey client.

## [1.1.0] - 2026-09-06

### Features

- **Valkey credentials.** `ValkeyConfig` now models authentication as scalar
  `username` / `password` fields (mirroring `RedisConfig`), translated into
  glide's `ServerCredentials` by `AsyncValkeyHandler.connect()`. Pass them via
  `valkey_config={"username": ..., "password": ...}`.

## [1.0.0] - 2026-09-06

First stable release. The version jumps from `0.2.0` to `1.0.0` (not `0.3.0`)
because the codebase underwent a full architecture rewrite after the `0.2.0`
tag: the tag points at a stale pre-rewrite commit, and the version string was
never bumped through ~12,100 insertions across 50 files. This release reflects
the settled, reviewed code at `HEAD`, not an incremental patch on top of
`0.2.0`.

### Features

- **Architecture rewrite.** Shared queue/worker/event machinery extracted into a
  new `AsyncLoggingHandler` base with no sink of its own; the console output
  moved out into a peer `ConsoleBackend`. The public hierarchy is now
  `AsyncLoggingHandler` (machinery) → `AsyncBaseHandler` (registers
  `ConsoleBackend` when `stdout_enable=True`) → `AsyncBrokerHandler` (abstract
  broker base) → `AsyncRedisHandler` / `AsyncValkeyHandler`.
- **Typed configuration.** Frozen dataclasses `LoggingConfig`, `RedisConfig`,
  and `ValkeyConfig` as the single source of truth for handler options.
  `RedisConfig` was reconciled with the `redis.asyncio` 8.x option surface, and
  `ValkeyConfig` mirrors the scalar `GlideClientConfiguration` options.
- **Restartable lifecycle.** `start_logging()` / `stop_logging()` can be run
  multiple times on the same event loop; graceful shutdown drains queues with a
  configurable timeout and drains backends concurrently.
- **Reliability.** Bounded queues with a drop-and-report overflow policy;
  broker connect retry with exponential backoff and jitter; ack-in-finally
  worker teardown; disconnect-on-send-failure; error reporting via a
  configurable `error_handler` with a module-logger fallback.
- **Stdlib teardown contract.** `close()`, `flush()`, and `handleError()`
  overrides honor the standard-library logging teardown contract; off-loop
  `emit` drops-and-reports instead of raising.

### Changed

- **Public API** (`__all__`): `AsyncLoggingHandler`, `AsyncBaseHandler`,
  `AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`,
  `ConsoleBackend`, `ScietexFormatter`, plus config types `LoggingConfig`,
  `RedisConfig`, `ValkeyConfig`. `AsyncRedisHandler` / `AsyncValkeyHandler` are
  exposed conditionally on their optional dependencies.
- **Extensibility.** `formatter=` injection (console-only scope) and the
  `register_backend` / `register_status_reporter` extension seams.

### Fixed

- Resolved all actionable findings from the deep architecture review cycles
  (AR-005 through AR-116): P1 worker-failure and lifecycle-contract issues,
  P2 lifecycle/architecture refactors, and P3 polish findings.

### Docs

- Architecture map (`docs/architecture/`): overview, structure, components,
  dependencies, data-flow, lifecycle, and hotspots documents.
- User guides: `configuration.md`, `advanced.md`, `backends.md`, `examples.md`.
- Expanded runnable examples covering every backend, custom backends and
  formatters, error handling, and the restartable lifecycle.
- Deep architecture reviews: `2026-09-04.md`, `2026-09-05.md`,
  `2026-09-06.md`.

### Internal

- 110 tests passing; `ruff` and `ty` clean across the package.
- All findings from the architecture review cycles (AR-005..AR-116) are
  resolved and the review cycle is closed.
