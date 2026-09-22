"""Textual TUI app that displays live scietex.logging output.

Press ``g`` to toggle log generation, ``q`` to quit. Press ``t`` to cycle the
Scietex themes (light, dark, monochrome); the app also follows any theme picked
from Textual's command palette (``Ctrl+P`` -> "theme").

This example shows how to route the asynchronous logging machinery into a
Textual widget. The key idea: ``ConsoleBackend`` writes to ``sys.stdout``, which
Textual owns, so it cannot be reused directly. Instead a small custom backend
posts each formatted record into the app as a thread-safe Textual message, and
the app renders it with ``Text.from_ansi`` so the theme's truecolor survives.

Two-way theme integration:

* The three Scietex themes are registered as Textual themes, so they appear in
  the command palette and drive the whole UI (borders, header, footer).
* The log formatter follows whatever theme is active: on every theme change the
  app converts the current Textual theme with ``from_textual_theme`` and swaps
  the formatter, so the log colors always match the UI.

Requires Textual (the ``test`` extra)::

    uv run --extra test python examples/textual_log_viewer.py
"""

import asyncio
import logging
import random

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Footer, Header, RichLog

from scietex.logging import (
    MONOCHROME,
    SCIETEX_DARK,
    SCIETEX_LIGHT,
    AsyncLoggingHandler,
    LoggingTheme,
    ScietexFormatter,
    from_textual_theme,
)
from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus

_QUEUE_TEXTUAL = "_textual"

_LEVELS = [
    logging.DEBUG,
    logging.INFO,
    logging.INFO,
    logging.WARNING,
    logging.ERROR,
    logging.CRITICAL,
]

_MESSAGES = [
    "Sensor poll completed",
    "Cache miss, refetching",
    "Connection pool resized",
    "Retrying upstream request",
    "Configuration reloaded",
    "Queue depth above threshold",
    "Heartbeat acknowledged",
    "Checksum mismatch, discarding frame",
]


def _to_textual_theme(theme: LoggingTheme) -> Theme:
    """Build a Textual ``Theme`` from a scietex ``LoggingTheme``.

    The Scietex palettes are already Textual-shaped, so the mapping is direct:
    the level colors become the semantic slots and the background/foreground
    carry over. Registering these makes the Scietex themes first-class Textual
    themes that drive the whole UI.

    The brand yellow is an *accent*, not a surface: in the brand deck it appears
    only as the scietex.ru badge and the diagonal panel, while panels and the
    header sit on the brand's neutral tones. So the yellow drives ``accent``
    (borders, focus) while ``primary``/``secondary`` use the brand neutrals,
    which is what Textual tints the header, footer, and panel surfaces with.
    ``panel`` is left unset so Textual derives it from ``surface`` + ``primary``
    instead of painting a flat block of color.
    """
    palette = theme.palette
    dark = palette.background != SCIETEX_LIGHT.palette.background
    # Dark themes sit on the brand black with the dark-gray surface; the light
    # theme keeps a near-white surface so panels stay subtle on white.
    surface = palette.brand_dark_gray if dark else "#F5F5F5"
    return Theme(
        name=theme.name,
        primary=palette.brand_dark_gray,
        secondary=palette.brand_dark_gray,
        warning=palette.warning or palette.brand_yellow,
        error=palette.error or palette.brand_yellow,
        success=palette.info or palette.brand_yellow,
        accent=palette.brand_yellow,
        foreground=palette.foreground,
        background=palette.background,
        surface=surface,
        dark=dark,
    )


class LogLine(Message):
    """A formatted log line, safe to post from any thread."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class TextualLogHandler(AsyncLoggingHandler):
    """Async logging handler whose sink is a Textual app.

    Registers a single backend through the public ``register_backend`` API: a
    queue, a worker coroutine that formats each record and posts it to the app,
    and a drain hook that flushes the queue at shutdown. This mirrors the
    documented custom-backend pattern (see ``pure_machinery_handler.py``) rather
    than reaching into the private backend base class.
    """

    # Always non-None: __init__ installs a ScietexFormatter, so the worker can
    # format records without a None guard.
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
        """Drain the queue, posting each formatted record to the app.

        Formatting happens on the event-loop thread, so the shared record is
        never mutated off-loop. ``post_message`` is thread-safe, so the app
        receives the line regardless of which thread emitted the record.
        """
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
        """Wait for the queue to empty, reporting how the drain concluded."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.TIMEOUT)
        except Exception as exc:
            return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.ERROR, exc)
        return BackendDrainResult(_QUEUE_TEXTUAL, DrainStatus.COMPLETED)

    def set_theme(self, theme: LoggingTheme) -> None:
        """Swap the formatter's theme.

        The worker reads ``self.formatter`` at work time, so replacing it takes
        effect on the next record without restarting the worker. ``color`` is
        left to the theme, so a monochrome theme emits no ANSI.
        """
        self.formatter = ScietexFormatter(theme=theme)


class LogViewerApp(App):
    """Textual app showing live scietex.logging output."""

    TITLE = "scietex.logging"
    SUB_TITLE = "live log viewer"
    CSS = """
    RichLog {
        border: round $panel-lighten-1;
        padding: 0 1;
    }
    RichLog:focus {
        border: round $accent;
    }
    """

    BINDINGS = [
        ("g", "toggle_generator", "Toggle logs"),
        ("t", "cycle_theme", "Cycle theme"),
        ("q", "quit", "Quit"),
    ]

    # The log is a passive display, so it never takes focus: the border stays the
    # neutral panel tone and the yellow accent is reserved for the brand's thin
    # highlight lines rather than framing the whole viewport.
    AUTO_FOCUS = None

    # The Scietex themes, in cycle order. Registered as Textual themes so they
    # are selectable from the command palette and drive the whole UI.
    SCIETEX_THEMES = (SCIETEX_DARK, SCIETEX_LIGHT, MONOCHROME)

    def __init__(self) -> None:
        super().__init__()
        self._generating = False
        self._timer = None
        self._app_logger = logging.getLogger("TextualDemo")
        self._app_logger.setLevel(logging.DEBUG)
        self._handler = TextualLogHandler(self)
        self._app_logger.addHandler(self._handler)
        for theme in self.SCIETEX_THEMES:
            self.register_theme(_to_textual_theme(theme))

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(highlight=False, markup=False, wrap=True, max_lines=2000)
        yield Footer()

    async def on_mount(self) -> None:
        await self._handler.start_logging()
        self.theme = SCIETEX_DARK.name
        self._app_logger.info("Log viewer ready. 'g' toggles logs, 't' cycles themes, 'q' quits.")
        self.action_toggle_generator()

    async def on_unmount(self) -> None:
        await self._handler.stop_logging()
        self._app_logger.removeHandler(self._handler)

    def watch_theme(self, theme_name: str) -> None:
        """Follow the active Textual theme in the log formatter.

        Fires for any theme change, whether from the ``t`` binding or the command
        palette, so the log colors always match the UI. A registered Scietex theme
        is used directly, which preserves its ``color`` policy (monochrome emits
        no ANSI at all); any other Textual theme is converted on the fly.

        The theme change is logged after the formatter swap, so the announcement
        itself is rendered in the new theme's colors.
        """
        for theme in self.SCIETEX_THEMES:
            if theme.name == theme_name:
                self._handler.set_theme(theme)
                break
        else:
            textual_theme = self.get_theme(theme_name)
            if textual_theme is None:
                return
            self._handler.set_theme(from_textual_theme(textual_theme))
        self._app_logger.info("Theme changed to %s.", theme_name)

    @on(LogLine)
    def handle_log_line(self, message: LogLine) -> None:
        """Render a posted log line, decoding its ANSI truecolor."""
        self.query_one(RichLog).write(Text.from_ansi(message.text))

    def action_toggle_generator(self) -> None:
        """Start or pause the background log generator."""
        if self._timer is None:
            self._timer = self.set_interval(0.4, self._emit_random_log)
            self._generating = True
        elif self._generating:
            self._timer.pause()
            self._generating = False
        else:
            self._timer.resume()
            self._generating = True
        self.sub_title = "generating" if self._generating else "paused"

    def action_cycle_theme(self) -> None:
        """Advance to the next Scietex theme."""
        names = [theme.name for theme in self.SCIETEX_THEMES]
        index = names.index(self.theme) if self.theme in names else -1
        self.theme = names[(index + 1) % len(names)]

    def _emit_random_log(self) -> None:
        """Emit one random log record at a random level."""
        level = random.choice(_LEVELS)
        self._app_logger.log(level, random.choice(_MESSAGES))


if __name__ == "__main__":
    LogViewerApp().run()
