# Embedding in Textual

`scietex.logging` can drive a [Textual](https://textual.textualize.io/) TUI: route
the async logging machinery into a widget, and keep the log colors in sync with
the app's active theme. This page explains the pattern; the runnable version is
`examples/textual_log_viewer.py`.

Textual is a **test-only** dependency of `scietex.logging` — the library never
imports it. Install it with the `test` extra:

```bash
uv run --extra test python examples/textual_log_viewer.py
```

## Why not `ConsoleHandler`?

`ConsoleHandler` writes to `sys.stdout`, which Textual owns while the app runs,
so it cannot be reused directly. Instead, register a small custom backend that
posts each formatted record into the app as a thread-safe Textual message, and
render it with `Text.from_ansi` so the theme's truecolor survives.

## The pattern

Subclass the pure-machinery base `AsyncLoggingHandler` and register one backend
through the public `register_backend` API — a queue, a worker coroutine, and a
drain hook:

```python
import asyncio
import logging

from textual.app import App
from textual.message import Message

from scietex.logging import AsyncLoggingHandler, LoggingTheme, SCIETEX_DARK, ScietexFormatter
from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus

_QUEUE_TEXTUAL = "_textual"


class LogLine(Message):
    """A formatted log line, safe to post from any thread."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class TextualLogHandler(AsyncLoggingHandler):
    formatter: logging.Formatter

    def __init__(self, app: App, *, theme: LoggingTheme = SCIETEX_DARK, **kwargs) -> None:
        super().__init__(**kwargs)
        self._app = app
        self.formatter = ScietexFormatter(theme=theme)
        self._queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(
            maxsize=self.config.queue_maxsize
        )
        self.register_backend(_QUEUE_TEXTUAL, self._queue, self._worker, self._drain)

    async def _worker(self) -> None:
        while self.logging_running_event.is_set() or not self._queue.empty():
            try:
                record = await asyncio.wait_for(self._queue.get(), 1)
            except asyncio.TimeoutError:
                continue
            try:
                self._app.post_message(LogLine(self.formatter.format(record)))
            except Exception as exc:
                # A buggy formatter must not kill the worker; report and keep draining.
                self._report_error(record, exc)
            finally:
                self._queue.task_done()

    async def _drain(self, timeout: float) -> BackendDrainResult:
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.ERROR, exc)
        return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.COMPLETED)

    def set_theme(self, theme: LoggingTheme) -> None:
        # The worker reads self.formatter at work time, so replacing it takes
        # effect on the next record without restarting the worker.
        self.formatter = ScietexFormatter(theme=theme)
```

Key points:

- **Format on the loop.** The worker formats each record on the event-loop
  thread, so the shared `LogRecord` is never mutated off-loop.
- **`post_message` is thread-safe.** The app receives the line regardless of
  which thread emitted the record.
- **A buggy formatter must not kill the worker.** Catch, report via
  `_report_error`, and keep draining.
- **`set_theme` swaps the formatter.** Because the worker reads
  `self.formatter` at work time, the swap takes effect on the next record
  without restarting the worker.

## Wiring it into the app

Start the handler in `on_mount`, stop it in `on_unmount`, and render posted
lines into a `RichLog`:

```python
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, RichLog
from rich.text import Text


class LogViewerApp(App):
    BINDINGS = [
        ("g", "toggle_generator", "Toggle logs"),
        ("t", "cycle_theme", "Cycle theme"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._app_logger = logging.getLogger("TextualDemo")
        self._app_logger.setLevel(logging.DEBUG)
        self._handler = TextualLogHandler(self)
        self._app_logger.addHandler(self._handler)

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=False, markup=False, wrap=True, max_lines=2000)
        yield Footer()

    async def on_mount(self) -> None:
        await self._handler.start_logging()

    async def on_unmount(self) -> None:
        await self._handler.stop_logging()
        self._app_logger.removeHandler(self._handler)

    @on(LogLine)
    def handle_log_line(self, message: LogLine) -> None:
        self.query_one(RichLog).write(Text.from_ansi(message.text))
```

This is an abbreviated fragment — the theme registration and `watch_theme`
override are shown below, and the full app is in the example.

## Two-way theme integration

The example integrates themes in both directions:

1. **Scietex themes as Textual themes.** Register the Scietex palettes as
   Textual themes so they appear in the command palette and drive the whole UI
   (borders, header, footer). The Scietex palettes are already Textual-shaped, so
   the mapping is direct — level colors become semantic slots, and the
   background/foreground carry over.

2. **Follow the active theme.** Override `watch_theme` so the log formatter
   always matches the UI. A registered Scietex theme is used directly, which
   preserves its `color` policy (a monochrome theme emits no ANSI at all); any
   other Textual theme is converted on the fly with `from_textual_theme`:

```python
def watch_theme(self, theme_name: str) -> None:
    for theme in self.SCIETEX_THEMES:
        if theme.name == theme_name:
            self._handler.set_theme(theme)
            break
    else:
        textual_theme = self.get_theme(theme_name)
        if textual_theme is None:
            return
        self._handler.set_theme(from_textual_theme(textual_theme))
```

Using the original `LoggingTheme` for Scietex themes (rather than round-tripping
through `from_textual_theme`) preserves `color=False` for the monochrome theme.
See {doc}`themes` for the theme API.

## Full example

`examples/textual_log_viewer.py` is the complete app: a `RichLog` viewer, a
background log generator, and theme cycling. Keys: `g` toggles the generator,
`t` cycles themes, `q` quits.
