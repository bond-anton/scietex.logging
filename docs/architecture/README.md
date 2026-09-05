# scietex.logging — Architecture Map

This directory contains a factual, code-derived structural map of the
`scietex.logging` package. It is intended as input for a later deep
architectural review; it does **not** propose refactorings or judge quality.

## Project Overview

`scietex.logging` is an asynchronous (asyncio-based) logging package for
Python >= 3.10. It plugs into the standard-library `logging` framework via
custom `logging.Handler` subclasses. Log records are queued in `asyncio.Queue`
objects and drained by background worker coroutines, so application code never
blocks on I/O.

Three backends are supported, layered on a class hierarchy whose shared
machinery is separated from the sinks:

- **Console** (stdout) — always available, no extra dependency. A peer backend
  (`ConsoleBackend`) registered by `AsyncBaseHandler`.
- **Redis** (streams) — optional, requires the `redis` package.
- **Valkey** (streams) — optional, requires the `valkey-glide` package.

The package is small: ~1085 lines of source across 8 modules under
`src/scietex/logging/`.

## Document Index

| Document | Contents |
|---|---|
| [overview.md](./overview.md) | Major subsystems, responsibilities, interactions, entry points, runtime processes |
| [structure.md](./structure.md) | Repository/package layout and module responsibilities |
| [components.md](./components.md) | Per-component purpose, classes, interfaces, dependencies |
| [dependencies.md](./dependencies.md) | Dependency direction, core→infrastructure edges, chains |
| [data-flow.md](./data-flow.md) | End-to-end data flows, queues, async boundaries |
| [lifecycle.md](./lifecycle.md) | Startup, operation, shutdown, workers, resource ownership |
| [hotspots.md](./hotspots.md) | Areas flagged for deeper architectural investigation |

## Key Facts (quick reference)

- **Package**: `scietex.logging`, version `0.2.0` (`src/scietex/logging/__init__.py:100`)
- **Python**: `>=3.10` (`pyproject.toml`)
- **Build**: setuptools, `src/` layout; package data ships `py.typed`
- **Runtime deps**: none (base); `redis>=5.0.0` (`[redis]`), `valkey-glide~=2.5.0` (`[valkey]`)
- **Public API** (`__init__.py`): `AsyncBaseHandler`, `AsyncBrokerHandler`,
  `ScietexFormatter`; `AsyncRedisHandler` / `AsyncValkeyHandler` added
  conditionally on successful import
- **Class hierarchy**: `logging.Handler` → `AsyncLoggingHandler` (pure
  machinery, no sink) → `AsyncBaseHandler` (registers `ConsoleBackend` peer) →
  `AsyncBrokerHandler` → {`AsyncRedisHandler`, `AsyncValkeyHandler`}
- **Tests**: pytest + pytest-asyncio; Redis tests require a live server, and the
  Valkey end-to-end test skips when no Valkey server is reachable
- **Tooling**: uv (lockfile), tox (format/lint/type/py314), ruff, `ty` type checker
