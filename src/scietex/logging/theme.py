"""ANSI color helpers, palettes, and themes for scietex.logging formatters.

This module is a stdlib-only neutral leaf, following the same rule as
``config.py``: it imports only the standard library, so formatters and handlers
can depend on it without creating an import cycle. Colors are emitted as 24-bit
truecolor SGR sequences (``\\x1b[38;2;R;G;Bm``) rather than indexed 8/16-color
codes because the brand palette is defined by exact hex values that no indexed
color table can reproduce faithfully.

Color is strictly opt-in. ``resolve_color`` enables it only for a terminal
stream, or on an explicit ``FORCE_COLOR``/``force_color`` request, and never
auto-enables it for redirected or file output, where escape sequences would
leak into the written logs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


def _parse_hex(hex_color: str) -> tuple[int, int, int]:
    """Parse a ``#RRGGBB`` or ``RRGGBB`` color into an ``(r, g, b)`` tuple."""
    raw = hex_color[1:] if hex_color.startswith("#") else hex_color
    if len(raw) != 6:
        raise ValueError(f"invalid hex color {hex_color!r}: expected 6 hex digits")
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError as exc:
        raise ValueError(f"invalid hex color {hex_color!r}: expected 6 hex digits") from exc


def ansi_fg(hex_color: str) -> str:
    """Return the truecolor SGR sequence that sets the foreground to ``hex_color``.

    Args:
        hex_color (str): A ``#RRGGBB`` or ``RRGGBB`` color.

    Returns:
        str: The ``\\x1b[38;2;R;G;Bm`` escape sequence.
    """
    r, g, b = _parse_hex(hex_color)
    return f"\x1b[38;2;{r};{g};{b}m"


def ansi_bg(hex_color: str) -> str:
    """Return the truecolor SGR sequence that sets the background to ``hex_color``.

    Args:
        hex_color (str): A ``#RRGGBB`` or ``RRGGBB`` color.

    Returns:
        str: The ``\\x1b[48;2;R;G;Bm`` escape sequence.
    """
    r, g, b = _parse_hex(hex_color)
    return f"\x1b[48;2;{r};{g};{b}m"


@dataclass(frozen=True)
class Palette:
    """Color assignments for a theme's structural log elements.

    The level slots (``debug``, ``info``, ``warning``, ``error``, ``critical``,
    ``critical_bg``, ``logger_name``) are ``str | None``: ``None`` means "do not
    color this element", which is how the monochrome theme renders plain text.
    ``background`` and ``foreground`` are required plain ``str``; the three brand
    colors are plain ``str`` carrying their exact hex values as defaults so any
    palette can reference them without repeating the literals.

    Attributes:
        background (str): Terminal background color.
        foreground (str): Default foreground (fallback level) color.
        brand_yellow (str): The brand yellow (default "#FFDB1C").
        brand_dark_gray (str): The brand dark gray (default "#31313B").
        brand_black (str): The brand black (default "#1F202A").
        debug (str | None): DEBUG-level color (default None).
        info (str | None): INFO-level color (default None).
        warning (str | None): WARNING-level color (default None).
        error (str | None): ERROR-level color (default None).
        critical (str | None): CRITICAL-level foreground color (default None).
        critical_bg (str | None): CRITICAL-level background color (default None).
        logger_name (str | None): Logger-name color (default None).
    """

    background: str
    foreground: str
    brand_yellow: str = "#FFDB1C"
    brand_dark_gray: str = "#31313B"
    brand_black: str = "#1F202A"
    debug: str | None = None
    info: str | None = None
    warning: str | None = None
    error: str | None = None
    critical: str | None = None
    critical_bg: str | None = None
    logger_name: str | None = None

    def level_color(self, levelno: int) -> str | None:
        """Return the palette color assigned to a stdlib logging level.

        Unknown levels fall back to ``foreground``. A known level whose slot is
        unset returns ``None`` (e.g. the monochrome palette).

        Args:
            levelno (int): A stdlib ``logging`` level such as ``logging.DEBUG``.

        Returns:
            str | None: The mapped color, ``foreground`` for unknown levels, or
                ``None`` when the level's field is unset.
        """
        colors: dict[int, str | None] = {
            logging.DEBUG: self.debug,
            logging.INFO: self.info,
            logging.WARNING: self.warning,
            logging.ERROR: self.error,
            logging.CRITICAL: self.critical,
        }
        return colors.get(levelno, self.foreground)


@dataclass(frozen=True)
class LoggingTheme:
    """A named rendering theme binding a ``Palette`` to a color policy.

    Attributes:
        name (str): Unique theme identifier.
        palette (Palette): The color assignments for this theme.
        color (bool): Whether ANSI color is emitted at all (default True).
    """

    name: str
    palette: Palette
    color: bool = True


@dataclass(frozen=True)
class MonochromeTheme(LoggingTheme):
    """Colorless theme that renders plain text with no ANSI sequences."""

    name: str = "monochrome"
    palette: Palette = field(
        default_factory=lambda: Palette(
            background="#1F202A",
            foreground="#FFFFFF",
        )
    )
    color: bool = False


@dataclass(frozen=True)
class ScietexLight(LoggingTheme):
    """Light Scietex brand theme on a white background."""

    name: str = "scietex-light"
    palette: Palette = field(
        default_factory=lambda: Palette(
            background="#FFFFFF",
            foreground="#1F202A",
            debug="#31313B",
            info="#0CADB3",
            warning="#E8A317",
            error="#FF0000",
            critical="#FFFFFF",
            critical_bg="#FF0000",
            logger_name="#FFDB1C",
        )
    )
    color: bool = True


@dataclass(frozen=True)
class ScietexDark(LoggingTheme):
    """Dark Scietex brand theme on the brand-black background."""

    name: str = "scietex-dark"
    palette: Palette = field(
        default_factory=lambda: Palette(
            background="#1F202A",
            foreground="#FFFFFF",
            debug="#C9C9D4",
            info="#0CADB3",
            warning="#E8A317",
            error="#FF0000",
            critical="#FFFFFF",
            critical_bg="#FF0000",
            logger_name="#FFDB1C",
        )
    )
    color: bool = True


MONOCHROME = MonochromeTheme()
SCIETEX_LIGHT = ScietexLight()
SCIETEX_DARK = ScietexDark()


def resolve_color(
    stream: object | None = None,
    *,
    color: bool | None = None,
    force_color: bool | None = None,
    no_color: bool | None = None,
) -> bool:
    """Decide whether ANSI color should be emitted for the given stream.

    Precedence, highest first: ``force_color``, then ``no_color``, then an
    explicit ``color`` override, then the ``FORCE_COLOR`` / ``NO_COLOR``
    environment variables, and finally TTY detection on ``stream``. A ``None``
    stream never yields color from TTY detection, so file and redirected output
    stay plain unless ``force_color``/``FORCE_COLOR`` overrides.

    Args:
        stream (object | None): The output stream. ``None`` disables color.
        color (bool | None): Explicit on/off override for the caller.
        force_color (bool | None): Force color on regardless of the stream.
        no_color (bool | None): Force color off regardless of the stream.

    Returns:
        bool: True when ANSI color sequences should be emitted.
    """
    if force_color is True:
        return True
    if no_color is True:
        return False
    if color is not None:
        return bool(color)
    force_env = os.environ.get("FORCE_COLOR")
    if force_env is not None and force_env != "0":
        return True
    if "NO_COLOR" in os.environ:
        return False
    if stream is None:
        return False
    try:
        isatty = getattr(stream, "isatty", None)
        if isatty is None:
            return False
        return bool(isatty())
    except Exception:
        # A stream whose isatty raises cannot be trusted to be a terminal.
        return False
