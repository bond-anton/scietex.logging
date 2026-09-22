"""Convert Textual themes into ``scietex.logging`` themes.

Textual's ``Theme`` is a plain dataclass of primitives, so this module reads it
duck-typed via ``getattr`` and never imports Textual. Callers pass a real
``textual.theme.Theme`` (e.g. ``BUILTIN_THEMES["nord"]``) or any object exposing
the same attribute names::

    from textual.theme import BUILTIN_THEMES
    from scietex.logging import from_textual_theme

    theme = from_textual_theme(BUILTIN_THEMES["nord"])

The mapping is hybrid: base palette slots come straight from the theme's own
colors, while the slots Textual derives (``debug`` from ``$text-muted``,
``critical``/``critical_bg`` from the error color) are reproduced with the
ported CIE-Lab math in ``_color``. The result is a plain ``LoggingTheme``
consumable by any formatter.

Like ``theme.py``, this is a stdlib-only module: it imports only the standard
library and sibling stdlib-only leaves.
"""

from __future__ import annotations

from collections.abc import Mapping

from ._color import Color, ColorParseError
from .theme import LoggingTheme, Palette

# Textual's ColorSystem defaults, mirrored so a theme that omits a color
# resolves to the same value Textual would use.
_DEFAULT_DARK_BACKGROUND = "#121212"
_DEFAULT_LIGHT_BACKGROUND = "#efefef"
_DEFAULT_TEXT_MUTED = "auto 60%"

# How much to darken the error color for a CRITICAL background on dark themes.
# Textual has no direct equivalent; this mirrors the ScietexDark relationship
# between error (#FF7B72) and critical_bg (#B62324).
_DEFAULT_CRITICAL_DARKEN = 0.12


def _resolve(
    theme: object,
    variables: Mapping[str, str],
    key: str,
    *,
    fallback: str | None,
    theme_name: str,
    ansi_palette: Mapping[str, str] | None = None,
) -> Color:
    """Resolve one base color, honoring ``variables`` overrides then the theme.

    Args:
        theme (object): The duck-typed Textual theme.
        variables (Mapping[str, str]): The theme's ``variables`` overrides.
        key (str): The color name, e.g. ``"primary"``.
        fallback (str | None): Value to use when neither source provides one.
        theme_name (str): Theme name, for error messages.
        ansi_palette (Mapping[str, str] | None): Hex values for ``ansi_*`` tokens.

    Returns:
        Color: The resolved color.

    Raises:
        ValueError: If no source provides a value or it cannot be parsed.
    """
    raw = variables.get(key) or getattr(theme, key, None) or fallback
    if raw is None:
        raise ValueError(f"textual theme {theme_name!r} has no {key!r} color")
    if raw.startswith("ansi_"):
        raw = _resolve_ansi(raw, theme_name=theme_name, ansi_palette=ansi_palette)
    return _parse_slot(raw, theme_name=theme_name, slot=key)


def _parse_slot(raw: str, *, theme_name: str, slot: str) -> Color:
    """Parse a color token, wrapping parse failures with theme/slot context."""
    try:
        return Color.parse(raw)
    except ColorParseError as exc:
        raise ValueError(
            f"textual theme {theme_name!r} has an unparseable {slot!r} color: {raw!r}"
        ) from exc


def _canonical(color: Color) -> Color:
    """Apply Textual's ``lighten(0)`` Lab round-trip to a base color.

    ``ColorSystem._generate`` emits every base color as
    ``color.lighten(luminosity_delta).hex``, and the ``delta == 0`` shade is the
    base value. That round-trip through CIE-Lab is not the identity: it can
    shift a channel by one, so ``#ffa62b`` becomes ``#FEA62B``. Reproducing it
    keeps the converted palette byte-identical to Textual's own output.
    """
    return color.lighten(0)


def _flatten(color: Color, background: Color) -> str:
    """Composite a possibly translucent color over ``background`` into ``#RRGGBB``.

    Terminal truecolor has no alpha channel, so a translucent token such as
    Textual's ``auto 60%`` is resolved to the opaque color it would render as.
    """
    if color.a >= 1.0:
        return color.hex6
    return color.blend(background, color.a, alpha=1.0).hex6


def _text_muted(
    variables: Mapping[str, str],
    background: Color,
    *,
    theme_name: str,
    ansi_palette: Mapping[str, str] | None,
) -> str:
    """Resolve the ``debug`` slot from Textual's ``$text-muted`` token.

    ``$text-muted`` defaults to ``auto 60%``: a contrast color (black or white,
    whichever contrasts the background) at 60% alpha. Terminal truecolor has no
    alpha, so the token is composited over the background into an opaque hex.
    """
    token = variables.get("text-muted", _DEFAULT_TEXT_MUTED)
    if token.startswith("ansi_"):
        return _resolve_ansi(token, theme_name=theme_name, ansi_palette=ansi_palette)
    if token == "auto" or token.startswith("auto "):
        alpha = 1.0
        if token != "auto":
            try:
                alpha = float(token[5:].rstrip("%")) / 100.0
            except ValueError as exc:
                raise ValueError(
                    f"textual theme {theme_name!r} has an unparseable 'text-muted' color: {token!r}"
                ) from exc
        return _flatten(background.get_contrast_text(alpha), background)
    return _flatten(_parse_slot(token, theme_name=theme_name, slot="text-muted"), background)


def _resolve_ansi(token: str, *, theme_name: str, ansi_palette: Mapping[str, str] | None) -> str:
    """Map an ``ansi_*`` token through a caller-supplied palette.

    scietex.logging emits 24-bit truecolor, so an ANSI slot name has no correct
    baked-in hex value: it depends on the terminal's palette. The caller must
    supply the mapping explicitly.
    """
    if ansi_palette is None:
        raise ValueError(
            f"textual theme {theme_name!r} uses ANSI color {token!r}, which has no "
            "fixed hex value; pass ansi_palette={'ansi_blue': '#...'} to resolve it"
        )
    try:
        return ansi_palette[token]
    except KeyError as exc:
        raise ValueError(f"ansi_palette has no entry for {token!r} (theme {theme_name!r})") from exc


def from_textual_theme(
    theme: object,
    *,
    name: str | None = None,
    color: bool = True,
    ansi_palette: Mapping[str, str] | None = None,
    critical_darken: float = _DEFAULT_CRITICAL_DARKEN,
) -> LoggingTheme:
    """Build a ``LoggingTheme`` from a Textual theme.

    Args:
        theme (object): A Textual ``Theme`` or any object exposing the same
            attributes (``name``, ``primary``, ``secondary``, ``warning``,
            ``error``, ``success``, ``accent``, ``foreground``, ``background``,
            ``dark``, ``variables``, ``ansi``).
        name (str | None): Override for the resulting theme name. Defaults to
            the source theme's ``name``, or ``"textual"`` when absent.
        color (bool): Whether the resulting theme emits ANSI color (default True).
        ansi_palette (Mapping[str, str] | None): Hex values for ``ansi_*`` tokens.
            Required only for ANSI themes (``ansi=True``), which have no fixed
            hex colors.
        critical_darken (float): CIE-Lab darkening applied to the error color for
            the CRITICAL background on dark themes (default 0.12).

    Returns:
        LoggingTheme: A theme whose palette maps the Textual colors onto the
            log-element slots.

    Raises:
        ValueError: If the theme has no ``primary`` color, uses ANSI colors
            without an ``ansi_palette``, or contains an unparseable color.
    """
    theme_name = name or getattr(theme, "name", None) or "textual"
    variables: Mapping[str, str] = getattr(theme, "variables", None) or {}
    dark = bool(getattr(theme, "dark", True))

    if getattr(theme, "ansi", False) and ansi_palette is None:
        raise ValueError(
            f"textual theme {theme_name!r} is an ANSI theme; pass ansi_palette "
            "to map its ansi_* colors to hex values"
        )

    primary = _canonical(
        _resolve(
            theme,
            variables,
            "primary",
            fallback=None,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    secondary = _canonical(
        _resolve(
            theme,
            variables,
            "secondary",
            fallback=primary.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    warning = _canonical(
        _resolve(
            theme,
            variables,
            "warning",
            fallback=primary.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    error = _canonical(
        _resolve(
            theme,
            variables,
            "error",
            fallback=secondary.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    success = _canonical(
        _resolve(
            theme,
            variables,
            "success",
            fallback=secondary.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    accent = _canonical(
        _resolve(
            theme,
            variables,
            "accent",
            fallback=primary.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    background = _canonical(
        _resolve(
            theme,
            variables,
            "background",
            fallback=_DEFAULT_DARK_BACKGROUND if dark else _DEFAULT_LIGHT_BACKGROUND,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )
    foreground = _canonical(
        _resolve(
            theme,
            variables,
            "foreground",
            fallback=background.inverse.hex6,
            theme_name=theme_name,
            ansi_palette=ansi_palette,
        )
    )

    # CRITICAL renders as contrast text on a red background. On dark themes the
    # error color is darkened so the white text stays legible; on light themes
    # the error color is already dark enough to carry black text.
    critical_bg_color = error.darken(critical_darken) if dark else error
    critical = critical_bg_color.get_contrast_text(1.0)

    palette = Palette(
        background=background.hex6,
        foreground=foreground.hex6,
        debug=_text_muted(variables, background, theme_name=theme_name, ansi_palette=ansi_palette),
        info=success.hex6,
        warning=warning.hex6,
        error=error.hex6,
        critical=critical.hex6,
        critical_bg=critical_bg_color.hex6,
        logger_name=accent.hex6,
    )
    return LoggingTheme(name=theme_name, palette=palette, color=color)
