# Installation

## Requirements

- **Python 3.10 or newer** (`pyproject.toml` declares `requires-python = ">=3.10"`).
- No runtime dependencies for the base package — it uses only the standard
  library (`logging`, `asyncio`, `queue`, `dataclasses`).

## Base package

```bash
pip install scietex.logging
```

The base install provides the console and file backends, both formatters
(`ScietexFormatter`, `JsonFormatter`), the theme API, and the pure-machinery
base `AsyncLoggingHandler`. It has no third-party dependencies.

## Optional backends

Each broker backend is an extra, so you install only the client you need:

```bash
pip install scietex.logging[redis]   # Redis streams (redis)
pip install scietex.logging[valkey]  # Valkey streams (valkey-glide)
pip install scietex.logging[mqtt]    # MQTT topics (aiomqtt)
```

Or install every backend at once:

```bash
pip install scietex.logging[all]
```

| Extra | Installs | Enables |
|---|---|---|
| *(none)* | — | `ConsoleHandler`, `AsyncFileHandler` + rotation variants, `ScietexFormatter`, `JsonFormatter`, themes |
| `[redis]` | `redis>=5.0.0` | `AsyncRedisHandler` |
| `[valkey]` | `valkey-glide~=2.5.0` | `AsyncValkeyHandler` |
| `[mqtt]` | `aiomqtt~=2.5.0` | `AsyncMqttHandler` |
| `[all]` | all of the above | every backend |

## Development extras

```bash
pip install scietex.logging[dev]    # tox, redis, valkey, aiomqtt
pip install scietex.logging[lint]   # ruff, ty
pip install scietex.logging[test]   # pytest, pytest-asyncio, textual
```

The `[test]` extra includes `textual>=8.0.0`, which the Textual log-viewer
example and the theme-conversion tests use. Textual is a **test-only**
dependency: the library itself never imports it.

## Optional-dependency behavior

The broker handlers import their client at module top and raise a descriptive
`ImportError` when it is missing:

```
The 'redis' module is required to use this feature. Please install it by running:

    pip install scietex.logging[redis]
```

The package root guards these imports, so `import scietex.logging` succeeds
without any extras — `AsyncRedisHandler`, `AsyncValkeyHandler`, and
`AsyncMqttHandler` are simply absent from the namespace until their extra is
installed.

## Next steps

- {doc}`quickstart` — a five-minute console walkthrough.
- {doc}`../backends/index` — pick a backend.
