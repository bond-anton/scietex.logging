"""Tests for the theme module's palettes, color resolution, and ANSI helpers."""

import logging

import pytest

from scietex.logging import (
    MONOCHROME,
    SCIETEX_DARK,
    SCIETEX_LIGHT,
    MonochromeTheme,
    Palette,
    resolve_color,
)
from scietex.logging.formatter.scietex import ScietexFormatter
from scietex.logging.theme import RESET, ansi_bg, ansi_fg


class _FakeStream:
    """Minimal stream stub exposing only the ``isatty`` the resolver reads."""

    def __init__(self, isatty_result: bool = True) -> None:
        self._isatty_result = isatty_result

    def isatty(self) -> bool:
        return self._isatty_result


def test_resolve_color_force_color_beats_no_color_and_env(monkeypatch):
    """force_color=True wins over no_color=True, color=False, and the env."""
    monkeypatch.setenv("FORCE_COLOR", "0")
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_color(force_color=True, no_color=True, color=False) is True


def test_resolve_color_no_color_beats_color():
    """no_color=True wins over an explicit color=True."""
    assert resolve_color(no_color=True, color=True) is False


def test_resolve_color_explicit_color_beats_env(monkeypatch):
    """An explicit color override wins over FORCE_COLOR and NO_COLOR."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert resolve_color(color=False) is False
    monkeypatch.delenv("FORCE_COLOR")
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_color(color=True) is True


def test_resolve_color_force_color_env(monkeypatch):
    """FORCE_COLOR=1 forces color on a non-TTY stream; =0 leaves it plain."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert resolve_color(stream=_FakeStream(isatty_result=False)) is True
    monkeypatch.setenv("FORCE_COLOR", "0")
    assert resolve_color(stream=_FakeStream(isatty_result=False)) is False


def test_resolve_color_no_color_env(monkeypatch):
    """NO_COLOR disables color even for a TTY stream."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_color(stream=_FakeStream(isatty_result=True)) is False


def test_resolve_color_stream_tty_detection(monkeypatch):
    """TTY detection decides color only when no override and no env are set."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert resolve_color(stream=_FakeStream(isatty_result=True)) is True
    assert resolve_color(stream=_FakeStream(isatty_result=False)) is False
    assert resolve_color(stream=object()) is False  # no isatty attribute
    assert resolve_color(stream=None) is False


def test_palette_level_color_mapping():
    """level_color maps known levels to their fields; unknown falls back."""
    palette = Palette(
        background="#1F202A",
        foreground="#FFFFFF",
        debug="#6B7280",
        info="#116329",
        warning="#9A6700",
        error="#CF222E",
        critical="#FFDB1C",
    )
    assert palette.level_color(logging.DEBUG) == "#6B7280"
    assert palette.level_color(logging.INFO) == "#116329"
    assert palette.level_color(logging.WARNING) == "#9A6700"
    assert palette.level_color(logging.ERROR) == "#CF222E"
    assert palette.level_color(logging.CRITICAL) == "#FFDB1C"
    assert palette.level_color(12345) == "#FFFFFF"  # unknown level


def test_monochrome_theme_palette_has_no_level_colors():
    """MonochromeTheme leaves every level slot and logger_name unset."""
    for theme in (MONOCHROME, MonochromeTheme()):
        palette = theme.palette
        assert palette.debug is None
        assert palette.info is None
        assert palette.warning is None
        assert palette.error is None
        assert palette.critical is None
        assert palette.critical_bg is None
        assert palette.logger_name is None


def test_scietex_light_palette_hex_values():
    """ScietexLight mirrors the scietex.textual light palette."""
    palette = SCIETEX_LIGHT.palette
    assert palette.background == "#FFFFFF"
    assert palette.foreground == "#1F202A"
    assert palette.debug == "#31313B"
    assert palette.info == "#0CADB3"
    assert palette.warning == "#E8A317"
    assert palette.error == "#FF0000"
    assert palette.critical == "#FFFFFF"
    assert palette.critical_bg == "#FF0000"
    assert palette.logger_name == "#FFDB1C"


def test_scietex_dark_palette_hex_values():
    """ScietexDark mirrors the scietex.textual dark palette."""
    palette = SCIETEX_DARK.palette
    assert palette.background == "#1F202A"
    assert palette.foreground == "#FFFFFF"
    assert palette.debug == "#C9C9D4"
    assert palette.info == "#0CADB3"
    assert palette.warning == "#E8A317"
    assert palette.error == "#FF0000"
    assert palette.critical == "#FFFFFF"
    assert palette.critical_bg == "#FF0000"
    assert palette.logger_name == "#FFDB1C"


def test_formatter_default_monochrome_has_no_ansi():
    """The default (monochrome) formatter emits no ANSI escape sequences."""
    formatter = ScietexFormatter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message", None, None)
    assert "\x1b" not in formatter.format(record)


def test_formatter_dark_color_has_ansi_and_reset():
    """A dark color formatter paints output with ANSI sequences and RESET."""
    formatter = ScietexFormatter(theme=SCIETEX_DARK, color=True)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message", None, None)
    output = formatter.format(record)
    assert "\x1b" in output
    assert RESET in output


def test_formatter_light_color_has_ansi():
    """A light color formatter paints output with ANSI sequences."""
    formatter = ScietexFormatter(theme=SCIETEX_LIGHT, color=True)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message", None, None)
    assert "\x1b" in formatter.format(record)


def test_formatter_color_does_not_mutate_record():
    """format() in color mode never mutates the caller's LogRecord."""
    formatter = ScietexFormatter(theme=SCIETEX_DARK, color=True)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message %s", ("arg",), None)
    formatter.format(record)
    assert record.msg == "Test message %s"
    assert record.args == ("arg",)
    assert record.levelname == "INFO"


def test_ansi_fg_bg_sequences():
    """ansi_fg/ansi_bg emit truecolor SGR sequences for the given hex."""
    assert ansi_fg("#FFDB1C") == "\x1b[38;2;255;219;28m"
    assert ansi_bg("#FFDB1C") == "\x1b[48;2;255;219;28m"


def test_ansi_helpers_reject_malformed_hex():
    """Malformed hex colors raise ValueError."""
    with pytest.raises(ValueError):
        ansi_fg("#FFF")
    with pytest.raises(ValueError):
        ansi_bg("not-a-color")
