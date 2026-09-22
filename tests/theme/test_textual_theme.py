"""Tests for the Textual theme converter.

Textual is a test-only dependency (the ``test`` extra), so these tests import it
directly rather than mocking it. The fidelity tests compare the converted
palette against Textual's own ``ColorSystem.generate()`` output, which is the
authority on what a theme's colors actually are.
"""

import logging

import pytest
from textual.color import Color as TextualColor
from textual.theme import BUILTIN_THEMES, Theme

from scietex.logging import LoggingTheme, Palette, from_textual_theme
from scietex.logging.formatter.scietex import ScietexFormatter

NON_ANSI_THEMES = {name: t for name, t in BUILTIN_THEMES.items() if not t.ansi}


def _reference_text_muted(reference: dict[str, str], background: TextualColor) -> str:
    """Resolve ``$text-muted`` the way Textual renders it, for comparison."""
    token = reference["text-muted"]
    if token.startswith("auto"):
        alpha = 1.0 if token == "auto" else float(token[5:].rstrip("%")) / 100.0
        color = background.get_contrast_text(alpha)
    else:
        color = TextualColor.parse(token)
    if color.a < 1:
        return color.blend(background, color.a, alpha=1.0).hex6
    return color.hex6


def test_returns_logging_theme():
    """The converter returns a LoggingTheme with a Palette."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"])
    assert isinstance(theme, LoggingTheme)
    assert isinstance(theme.palette, Palette)
    assert theme.name == "nord"
    assert theme.color is True


def test_direct_mapping_uses_theme_attributes():
    """Base slots come straight from the theme's own colors.

    Values are the Lab-round-tripped form Textual itself emits, so ``error`` is
    ``#BE616A`` rather than the raw ``#BF616A``.
    """
    theme = from_textual_theme(BUILTIN_THEMES["nord"])
    palette = theme.palette
    assert palette.background == "#2E3440"
    assert palette.foreground == "#D8DEE9"
    assert palette.warning == "#EACB8B"
    assert palette.error == "#BE616A"
    assert palette.info == "#A3BE8C"
    assert palette.logger_name == "#B48EAD"


def test_name_override():
    """An explicit name overrides the source theme's name."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"], name="custom")
    assert theme.name == "custom"


def test_color_flag_override():
    """The color flag is forwarded to the resulting theme."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"], color=False)
    assert theme.color is False


def test_textual_defaults_fill_missing_slots():
    """A theme with only a primary color resolves Textual's documented defaults."""
    theme = from_textual_theme(Theme(name="minimal", primary="#0178D4"))
    palette = theme.palette
    assert palette.background == "#121212"
    assert palette.warning == "#0178D4"
    assert palette.error == "#0178D4"
    assert palette.info == "#0178D4"
    assert palette.logger_name == "#0178D4"


def test_light_defaults():
    """A light theme with no background uses Textual's light default."""
    theme = from_textual_theme(Theme(name="light", primary="#0178D4", dark=False))
    assert theme.palette.background == "#EFEFEF"


def test_missing_primary_raises():
    """A theme without a primary color cannot be converted."""
    # primary=None is the edge case under test; the type checker cannot express it.
    broken = Theme(name="broken", primary=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="no 'primary' color"):
        from_textual_theme(broken)


def test_variables_override_base_slot():
    """A theme's ``variables`` override wins over the base attribute."""
    theme = from_textual_theme(
        Theme(name="override", primary="#0178D4", variables={"warning": "#123456"})
    )
    assert theme.palette.warning == "#123456"


def test_text_muted_override_hex():
    """An explicit ``text-muted`` variable drives the debug slot."""
    theme = from_textual_theme(
        Theme(name="muted", primary="#0178D4", variables={"text-muted": "#FF0000"})
    )
    assert theme.palette.debug == "#FF0000"


def test_debug_is_composited_not_raw_contrast():
    """The debug slot is an opaque composite, distinct from background and contrast."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"])
    palette = theme.palette
    assert palette.debug != palette.background
    assert palette.debug != "#FFFFFF"
    assert palette.debug is not None
    assert len(palette.debug) == 7


def test_critical_pair_dark():
    """Dark themes darken the error color for the CRITICAL background."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"])
    palette = theme.palette
    assert palette.critical_bg != palette.error
    assert palette.critical == "#FFFFFF"
    assert palette.critical != palette.background


def test_critical_pair_light():
    """Light themes use the error color directly for the CRITICAL background."""
    theme = from_textual_theme(BUILTIN_THEMES["textual-light"])
    palette = theme.palette
    assert palette.critical_bg == palette.error
    assert palette.critical == "#FFFFFF"


def test_ansi_theme_without_palette_raises():
    """ANSI themes have no fixed hex colors and require an explicit palette."""
    with pytest.raises(ValueError, match="ANSI theme"):
        from_textual_theme(BUILTIN_THEMES["ansi-dark"])


def test_ansi_theme_with_palette_maps():
    """An ANSI theme converts when every ansi_* token is mapped to hex."""
    ansi_palette = {
        f"ansi_{name}": "#123456"
        for name in (
            "black",
            "red",
            "green",
            "yellow",
            "blue",
            "magenta",
            "cyan",
            "white",
            "bright_black",
            "bright_red",
            "bright_green",
            "bright_yellow",
            "bright_blue",
            "bright_magenta",
            "bright_cyan",
            "bright_white",
            "default",
        )
    }
    theme = from_textual_theme(BUILTIN_THEMES["ansi-dark"], ansi_palette=ansi_palette)
    assert theme.palette.background == "#123456"


def test_unparseable_color_raises_value_error():
    """A malformed color names the theme and the offending slot."""
    with pytest.raises(ValueError, match="unparseable 'warning' color"):
        from_textual_theme(Theme(name="bad", primary="#0178D4", warning="not-a-color"))


@pytest.mark.parametrize("name", sorted(NON_ANSI_THEMES))
def test_derived_values_match_color_system_generate(name):
    """Every non-ANSI built-in converts to Textual's own generated colors."""
    source = NON_ANSI_THEMES[name]
    reference = source.to_color_system().generate()
    palette = from_textual_theme(source).palette

    assert palette.background == reference["background"]
    assert palette.foreground == reference["foreground"]
    assert palette.warning == reference["warning"]
    assert palette.error == reference["error"]
    assert palette.info == reference["success"]
    assert palette.logger_name == reference["accent"]
    assert palette.debug == _reference_text_muted(
        reference, TextualColor.parse(reference["background"])
    )


@pytest.mark.parametrize("name", sorted(NON_ANSI_THEMES))
def test_all_palette_colors_are_parseable_hex(name):
    """Every converted slot is a 6-digit hex color the formatter can emit."""
    palette = from_textual_theme(NON_ANSI_THEMES[name]).palette
    for slot in (
        "background",
        "foreground",
        "debug",
        "info",
        "warning",
        "error",
        "critical",
        "critical_bg",
        "logger_name",
    ):
        value = getattr(palette, slot)
        assert value is not None, f"{name}.{slot} is None"
        assert len(value) == 7 and value.startswith("#"), f"{name}.{slot}={value!r}"


def test_converted_theme_renders_ansi():
    """A converted theme drives the formatter end to end."""
    theme = from_textual_theme(BUILTIN_THEMES["nord"])
    formatter = ScietexFormatter(theme=theme, color=True)
    record = logging.LogRecord("svc", logging.ERROR, "", 0, "boom", None, None)
    output = formatter.format(record)
    assert "\x1b[38;2;" in output
    assert "ERR" in output
    assert "boom" in output


def test_import_does_not_pull_textual():
    """Importing scietex.logging must not import textual."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scietex.logging, sys; "
            "assert 'textual' not in sys.modules, 'textual was imported'",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
