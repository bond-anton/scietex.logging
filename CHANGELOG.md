# Changelog

All notable changes to `scietex.logging` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
