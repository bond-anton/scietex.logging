"""Minimal color value object and CIE-Lab math, ported from Textual.

This module exists so ``theme_textual`` can reproduce Textual's derived colors
without importing Textual. The math is a verbatim port of
``textual/color.py`` (MIT, Textualize) — ``blend``, ``tint``, ``darken``,
``lighten``, ``get_contrast_text``, ``brightness``, and the ``rgb_to_lab`` /
``lab_to_rgb`` conversions. Do not "improve" it: any deviation silently changes
the derived colors and breaks fidelity with Textual's ``ColorSystem``.

Deliberate omissions relative to Textual's ``Color``: the ``ansi`` and ``auto``
fields, ``ansi_*`` parsing, named CSS colors, and the ``lru_cache`` decorators.
ANSI tokens are intercepted by ``theme_textual`` before reaching this module;
named colors in derived slots raise ``ColorParseError``.

Like ``theme.py`` and ``config.py``, this is a stdlib-only leaf: it imports only
the standard library so it can be depended on without creating an import cycle.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ColorParseError(ValueError):
    """Raised when a color string cannot be parsed."""


class Lab(NamedTuple):
    """A color in CIE-L*ab space."""

    L: float
    a: float
    b: float


_RE_COLOR = re.compile(
    r"^#([0-9a-fA-F]{3})$"
    r"|^#([0-9a-fA-F]{4})$"
    r"|^#([0-9a-fA-F]{6})$"
    r"|^#([0-9a-fA-F]{8})$"
    r"|^rgb\(\s*(\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3})\s*\)$"
    r"|^rgba\(\s*(\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*[\d.]+)\s*\)$"
    r"|^hsl\(\s*([\d.]+\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?)\s*\)$"
    r"|^hsla\(\s*([\d.]+\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+)\s*\)$"
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp ``value`` into the inclusive ``[minimum, maximum]`` range."""
    return max(minimum, min(maximum, value))


def _percentage_string_to_float(percentage: str) -> float:
    """Convert a CSS percentage such as ``"60%"`` into a ``0.0``-``1.0`` float."""
    return float(percentage.rstrip("%")) / 100.0


def _hls_to_rgb(h: float, lightness: float, s: float) -> tuple[float, float, float]:
    """Convert HLS components (each 0-1) to an RGB triple (each 0-1)."""
    if s == 0.0:
        return lightness, lightness, lightness
    if lightness <= 0.5:
        m2 = lightness * (1.0 + s)
    else:
        m2 = lightness + s - lightness * s
    m1 = 2.0 * lightness - m2

    def _to_rgb(hue: float) -> float:
        hue = hue % 1.0
        if hue < 1 / 6:
            return m1 + (m2 - m1) * hue * 6
        if hue < 1 / 2:
            return m2
        if hue < 2 / 3:
            return m1 + (m2 - m1) * (2 / 3 - hue) * 6
        return m1

    return _to_rgb(h + 1 / 3), _to_rgb(h), _to_rgb(h - 1 / 3)


class Color(NamedTuple):
    """An RGB color with an alpha component.

    Ported from Textual's ``Color`` minus the ``ansi``/``auto`` fields, which
    have no meaning for truecolor terminal output.
    """

    r: int
    g: int
    b: int
    a: float = 1.0

    @property
    def inverse(self) -> Color:
        """The inverse of this color (each channel subtracted from 255)."""
        r, g, b, a = self
        return Color(255 - r, 255 - g, 255 - b, a)

    @property
    def clamped(self) -> Color:
        """This color with all components clamped into their valid ranges."""
        r, g, b, a = self
        return Color(
            int(_clamp(r, 0, 255)),
            int(_clamp(g, 0, 255)),
            int(_clamp(b, 0, 255)),
            _clamp(a, 0.0, 1.0),
        )

    @property
    def normalized(self) -> tuple[float, float, float]:
        """The RGB components normalized to the 0.0-1.0 range."""
        r, g, b, _ = self
        return r / 255.0, g / 255.0, b / 255.0

    @property
    def brightness(self) -> float:
        """The human perceptual brightness, 0.0 (black) to 1.0 (white)."""
        r, g, b = self.normalized
        return (299 * r + 587 * g + 114 * b) / 1000

    @property
    def hex(self) -> str:
        """The color as ``#RRGGBB``, or ``#RRGGBBAA`` when not fully opaque."""
        r, g, b, a = self.clamped
        if a == 1:
            return f"#{r:02X}{g:02X}{b:02X}"
        return f"#{r:02X}{g:02X}{b:02X}{int(a * 255):02X}"

    @property
    def hex6(self) -> str:
        """The color as ``#RRGGBB``, ignoring alpha."""
        r, g, b, _ = self.clamped
        return f"#{r:02X}{g:02X}{b:02X}"

    @classmethod
    def parse(cls, color_text: str | Color) -> Color:
        """Parse a CSS-style color string into a ``Color``.

        Accepts ``#RGB``, ``#RGBA``, ``#RRGGBB``, ``#RRGGBBAA``, ``rgb()``,
        ``rgba()``, ``hsl()``, and ``hsla()``. A ``Color`` instance is returned
        unchanged. Named CSS colors and ``ansi_*`` tokens are not supported.

        Raises:
            ColorParseError: If the string is not a supported color format.
        """
        if isinstance(color_text, Color):
            return color_text
        match = _RE_COLOR.match(color_text)
        if match is None:
            raise ColorParseError(f"failed to parse {color_text!r} as a color")
        (
            rgb_hex_triple,
            rgb_hex_quad,
            rgb_hex,
            rgba_hex,
            rgb,
            rgba,
            hsl,
            hsla,
        ) = match.groups()

        if rgb_hex_triple is not None:
            r, g, b = rgb_hex_triple
            return cls(int(f"{r}{r}", 16), int(f"{g}{g}", 16), int(f"{b}{b}", 16))
        if rgb_hex_quad is not None:
            r, g, b, a = rgb_hex_quad
            return cls(
                int(f"{r}{r}", 16),
                int(f"{g}{g}", 16),
                int(f"{b}{b}", 16),
                int(f"{a}{a}", 16) / 255.0,
            )
        if rgb_hex is not None:
            r, g, b = (int(rgb_hex[i : i + 2], 16) for i in (0, 2, 4))
            return cls(r, g, b, 1.0)
        if rgba_hex is not None:
            r, g, b, a = (int(rgba_hex[i : i + 2], 16) for i in (0, 2, 4, 6))
            return cls(r, g, b, a / 255.0)
        if rgb is not None:
            r, g, b = (int(_clamp(int(float(v)), 0, 255)) for v in rgb.split(","))
            return cls(r, g, b, 1.0)
        if rgba is not None:
            float_r, float_g, float_b, float_a = (float(v) for v in rgba.split(","))
            return cls(
                int(_clamp(int(float_r), 0, 255)),
                int(_clamp(int(float_g), 0, 255)),
                int(_clamp(int(float_b), 0, 255)),
                _clamp(float_a, 0.0, 1.0),
            )
        if hsl is not None:
            h, s, lightness = hsl.split(",")
            return cls.from_hsl(
                float(h) % 360 / 360,
                _percentage_string_to_float(s),
                _percentage_string_to_float(lightness),
            )
        if hsla is not None:
            h, s, lightness, a = hsla.split(",")
            return cls.from_hsl(
                float(h) % 360 / 360,
                _percentage_string_to_float(s),
                _percentage_string_to_float(lightness),
            ).with_alpha(_clamp(float(a), 0.0, 1.0))
        raise ColorParseError(f"failed to parse {color_text!r} as a color")

    @classmethod
    def from_hsl(cls, h: float, s: float, lightness: float) -> Color:
        """Create a color from HSL components (each 0.0-1.0)."""
        r, g, b = _hls_to_rgb(h, lightness, s)
        return cls(int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))

    def with_alpha(self, alpha: float) -> Color:
        """Return this color with a new alpha component."""
        r, g, b, _ = self
        return Color(r, g, b, alpha)

    def blend(self, destination: Color, factor: float, alpha: float | None = None) -> Color:
        """Interpolate linearly toward ``destination`` by ``factor`` (0.0-1.0)."""
        if factor <= 0:
            return self
        if factor >= 1:
            return destination
        r1, g1, b1, a1 = self
        r2, g2, b2, a2 = destination
        new_alpha = a1 + (a2 - a1) * factor if alpha is None else alpha
        return Color(
            int(r1 + (r2 - r1) * factor),
            int(g1 + (g2 - g1) * factor),
            int(b1 + (b2 - b1) * factor),
            new_alpha,
        )

    def tint(self, color: Color) -> Color:
        """Blend toward ``color`` using ``color``'s alpha as the factor."""
        r1, g1, b1, a1 = self
        r2, g2, b2, a2 = color
        return Color(
            int(r1 + (r2 - r1) * a2),
            int(g1 + (g2 - g1) * a2),
            int(b1 + (b2 - b1) * a2),
            a1,
        )

    def darken(self, amount: float, alpha: float | None = None) -> Color:
        """Reduce CIE-L*ab luminance by ``amount * 100``."""
        lum, a, b = rgb_to_lab(self)
        lum -= amount * 100
        return lab_to_rgb(Lab(lum, a, b), self.a if alpha is None else alpha).clamped

    def lighten(self, amount: float, alpha: float | None = None) -> Color:
        """Increase CIE-L*ab luminance by ``amount * 100``."""
        return self.darken(-amount, alpha)

    def get_contrast_text(self, alpha: float = 0.95) -> Color:
        """Return off-white or off-black, whichever contrasts this color best."""
        return (WHITE if self.brightness < 0.5 else BLACK).with_alpha(alpha)


WHITE = Color(255, 255, 255)
BLACK = Color(0, 0, 0)


def rgb_to_lab(rgb: Color) -> Lab:
    """Convert an RGB color to CIE-L*ab (D65/2 degree illuminant)."""
    r, g, b = rgb.r / 255, rgb.g / 255, rgb.b / 255

    r = pow((r + 0.055) / 1.055, 2.4) if r > 0.04045 else r / 12.92
    g = pow((g + 0.055) / 1.055, 2.4) if g > 0.04045 else g / 12.92
    b = pow((b + 0.055) / 1.055, 2.4) if b > 0.04045 else b / 12.92

    x = (r * 41.24 + g * 35.76 + b * 18.05) / 95.047
    y = (r * 21.26 + g * 71.52 + b * 7.22) / 100
    z = (r * 1.93 + g * 11.92 + b * 95.05) / 108.883

    off = 16 / 116
    x = pow(x, 1 / 3) if x > 0.008856 else 7.787 * x + off
    y = pow(y, 1 / 3) if y > 0.008856 else 7.787 * y + off
    z = pow(z, 1 / 3) if z > 0.008856 else 7.787 * z + off

    return Lab(116 * y - 16, 500 * (x - y), 200 * (y - z))


def lab_to_rgb(lab: Lab, alpha: float = 1.0) -> Color:
    """Convert a CIE-L*ab color to RGB (D65/2 degree illuminant)."""
    y = (lab.L + 16) / 116
    x = lab.a / 500 + y
    z = y - lab.b / 200

    off = 16 / 116
    y = pow(y, 3) if y > 0.2068930344 else (y - off) / 7.787
    x = 0.95047 * pow(x, 3) if x > 0.2068930344 else 0.122059 * (x - off)
    z = 1.08883 * pow(z, 3) if z > 0.2068930344 else 0.139827 * (z - off)

    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    b = x * 0.0557 + y * -0.2040 + z * 1.0570

    r = 1.055 * pow(r, 1 / 2.4) - 0.055 if r > 0.0031308 else 12.92 * r
    g = 1.055 * pow(g, 1 / 2.4) - 0.055 if g > 0.0031308 else 12.92 * g
    b = 1.055 * pow(b, 1 / 2.4) - 0.055 if b > 0.0031308 else 12.92 * b

    return Color(int(r * 255), int(g * 255), int(b * 255), alpha)
