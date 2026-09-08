# Changelog

All notable changes to `scietex.logging` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-09-08

### Removed

- **Identity parameters**: `service_name`, `worker_id`, `instance_id`, the
  `worker_name` property, and the `resolve_instance_id` helper are removed from
  the package. Identity now comes solely from the standard-library logger name
  (`record.name`, set by `logging.getLogger("name")`). This is a hard breaking
  change.
- **`ScietexFormatter` signature**: now `ScietexFormatter(fmt=None, datefmt=None)`.
  The `service_name`/`instance_id`/`worker_id` arguments are gone, and the
  default format is
  `"%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"` — the
  `%(worker_name)s` token no longer exists; use `%(name)s`.
- **`LoggingConfig`**: no longer has `service_name`/`instance_id`/`worker_id`
  fields. No handler constructor accepts identity arguments.
- **Broker wire payload**: the `name` field is now `record.name` (the logger
  name), not `service_name:instance_id`.
- **`stdout_enable` parameter**: removed from every handler constructor and from
  `LoggingConfig`. Passing `stdout_enable=` now raises `TypeError`. File and
  broker handlers no longer auto-register a console sink — a handler emits only
  to its own backend.
- **`formatter` parameter**: removed from the pure-machinery base
  `AsyncLoggingHandler` and from every broker handler (`AsyncBrokerHandler`,
  `AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`). Passing
  `formatter=` to a broker handler or the base now raises `TypeError`. The
  `formatter` keyword is now console/file-specific: only `ConsoleHandler` and
  `AsyncFileHandler` (and its rotation variants) accept it, each owning its own
  `self.formatter` (default `ScietexFormatter`). Broker wire payloads are built
  from the record directly and were never affected by the formatter.
- **`LoggingConfig.backend_config`**: the `backend_config` field is removed from
  `LoggingConfig`, and the `backend_config=` keyword is removed from the
  pure-machinery base `AsyncLoggingHandler.__init__`. Passing `backend_config=`
  to `AsyncLoggingHandler` or `ConsoleHandler` now raises `TypeError`. Broker
  handlers (`AsyncBrokerHandler`, `AsyncRedisHandler`, `AsyncValkeyHandler`,
  `AsyncMqttHandler`) still accept `backend_config=` and now store the typed
  config as their own `self.backend_config` attribute (broker-owned, no longer
  forwarded to the base).

### Changed

- **`AsyncBaseHandler` renamed to `ConsoleHandler`**: the console handler is now
  a sibling of the file/broker handlers and registers the console backend
  unconditionally. Console output requires adding a `ConsoleHandler` to the
  logger explicitly, like any stdlib handler.
- **Handler re-parenting**: `AsyncFileHandler` and `AsyncBrokerHandler` now
  subclass `AsyncLoggingHandler` directly (no inherited console sink). Each
  registers only its own backend.
- **Namespaced built-in backend queue keys**: the built-in backends now register
  under `_`-prefixed queue keys (`"_console"`, `"_file"`, `"_redis"`,
  `"_valkey"`, `"_mqtt"`) instead of
  `"console"`/`"file"`/`"redis"`/`"valkey"`/`"mqtt"`, so a user-supplied
  `queue_name` can never collide with a built-in backend. Breaking for any code
  that reads `handler.log_queues["console"]` or a drain result's `.name` — those
  keys are now `"_console"` etc. The shutdown status text (e.g. "Console Logger
  has completed processing its queue.") is unchanged.
- **File backend owns its worker**: `FileBackend` now owns its worker and the
  full file lifecycle (lazy open, rollover, close-in-finally), mirroring
  `ConsoleBackend`. The four near-identical worker loops previously duplicated
  across `AsyncFileHandler` and its rotation variants are collapsed into one
  unified `FileBackend._worker`, with the rotation logic carried by
  `RotatingFileBackend`/`TimedRotatingFileBackend`/`WatchedFileBackend`. The
  handler classes are now thin constructor wrappers. Behavior and the public
  handler signatures are unchanged.
- **Shared `_QueueBackend` base**: the drain/status-reporting machinery
  (`drain`, `report_status`, `_status_record`, the `worker` property) is
  extracted from `ConsoleBackend` and `FileBackend` into a shared internal
  `_QueueBackend` base (`backend/_base.py`). `client_config` and a
  `_config_dict()` helper are likewise consolidated onto `AsyncBrokerHandler`.
- **`config.iso_timestamp()` helper**: the ISO-8601 UTC timestamp derivation is
  single-sourced into `config.iso_timestamp(created)`; `JsonFormatter`,
  `ScietexFormatter`, and the broker wire payload all use it.
- **`BackendDrainResult.display_name`**: drain results carry an optional
  `display_name` so shutdown-status records use an explicit label instead of
  deriving one from the queue-name constant. TIMEOUT/ERROR status text now
  capitalizes consistently (e.g. "Timeout while waiting for Valkey logger ...").

### Fixed

- **Injected-client `client_config`**: reading `client_config` on a broker
  handler constructed with an injected `client=` (and no config dict) now
  returns `{}` instead of raising `TypeError` from `dataclasses.asdict(None)`.

## [1.8.0] - 2026-09-07

### Added

- **`instance_id` parameter**: every handler constructor and `ScietexFormatter`
  now accept `instance_id: str | None = None` as the canonical identity field,
  rendered into `worker_name` as `service_name:instance_id`. It defaults to
  `"1"` when neither `instance_id` nor `worker_id` is supplied.

### Deprecated

- **`worker_id`**: the numeric `worker_id` parameter is deprecated in favor of
  `instance_id`. Passing `worker_id` emits a `DeprecationWarning` and its value
  is stringified into `instance_id`. Supplying both `worker_id` and
  `instance_id` raises `ValueError` (they are aliases for the same identity
  concept). `worker_id` is removed in v2.0.

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
