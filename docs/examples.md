# Examples

The `examples/` directory contains runnable scripts that progress from the happy
path to advanced capabilities. Every example follows the same lifecycle: create a
logger, add a handler, start the logging worker, log messages, then stop the
worker.

## Running examples

```bash
# Install dependencies
uv sync --all-extras

# Run any example
uv run python examples/example_name.py
```

## Example index

| Example | File | Backend / feature |
|---|---|---|
| Basic Console Logging | `examples/basic_console_logging.py` | `ConsoleHandler`, multiple levels |
| File Logging | `examples/file_logging.py` | `AsyncFileHandler`, plain text + JSON |
| Redis Logging | `examples/redis_logging.py` | `AsyncRedisHandler` |
| Valkey Logging | `examples/valkey_logging.py` | `AsyncValkeyHandler` |
| MQTT Logging | `examples/mqtt_logging.py` | `AsyncMqttHandler` |
| Console and Redis | `examples/console_and_redis_logging.py` | Multiple handlers on one logger |
| All Backends | `examples/all_backends.py` | Console + Redis + Valkey, explicit configs |
| Injected Client | `examples/injected_client.py` | `client=` injection (app-owned `GlideClient`) |
| Custom Formatter | `examples/custom_formatter.py` | `ScietexFormatter` with custom `fmt`/`datefmt` |
| Scietex Color Theme | `examples/scietex_color_theme.py` | Built-in `SCIETEX_LIGHT`/`SCIETEX_DARK` |
| Custom Palette | `examples/custom_palette.py` | Custom `Palette` + `LoggingTheme` |
| Error Handler and Queue Bounds | `examples/error_handler_and_queue_bounds.py` | `queue_maxsize` + `error_handler` |
| Custom Backend | `examples/custom_backend.py` | Subclass `AsyncBrokerHandler` |
| Pure Machinery Handler | `examples/pure_machinery_handler.py` | Subclass `AsyncLoggingHandler` directly |
| Restartable Lifecycle | `examples/restartable_lifecycle.py` | Start/stop/restart, timeouts |
| Textual Log Viewer | `examples/textual_log_viewer.py` | Textual TUI via `register_backend` |

## Server-backed examples

Redis, Valkey, MQTT, and the multi-backend examples need a running server:

- **Redis** — `redis_logging.py`, `console_and_redis_logging.py`,
  `all_backends.py`
- **Valkey** — `valkey_logging.py`, `all_backends.py`, `injected_client.py`
- **MQTT** — `mqtt_logging.py`

The remaining examples run with no external service.

## Textual Log Viewer

`examples/textual_log_viewer.py` routes the async logging machinery into a
Textual TUI. A custom backend (`TextualLogHandler`, built on the public
`register_backend` API) forwards records into a `RichLog` widget, the Scietex
themes are registered as Textual themes, and the app follows the active theme —
Scietex themes are used directly, others are converted with `from_textual_theme`.

```bash
uv run --extra test python examples/textual_log_viewer.py
```

Keys: `g` toggles the log generator, `t` cycles themes, `q` quits.

See {doc}`guide/textual` for a walkthrough of the embedding pattern.

## Customizing examples

Modify examples to explore features:

- Change the logger name passed to `logging.getLogger`.
- Adjust log levels.
- Configure custom formatters (`custom_formatter.py`).
- Add an `error_handler` callback and tune `queue_maxsize`
  (`error_handler_and_queue_bounds.py`).
- Build a custom backend by subclassing `AsyncBrokerHandler`
  (`custom_backend.py`) or `AsyncLoggingHandler` directly
  (`pure_machinery_handler.py`).
- Restart a handler across start/stop cycles (`restartable_lifecycle.py`).
- Add additional backends.
