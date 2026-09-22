# Themes and Color

`scietex.logging` ships a small theme API for colorizing console output. Color
is strictly **opt-in**: with no theme (the default) the output is monochrome and
byte-for-byte identical to a plain formatter.

## Built-in themes

The built-in themes are `MONOCHROME`, `SCIETEX_LIGHT`, and `SCIETEX_DARK`, each
binding a `Palette` of brand and per-level colors to a color policy.

Apply a theme directly to the formatter:

```python
from scietex.logging import SCIETEX_LIGHT, ScietexFormatter

formatter = ScietexFormatter(theme=SCIETEX_LIGHT, color=True)
```

Or to the console handler, which auto-detects color from `sys.stdout`:

```python
from scietex.logging import SCIETEX_DARK, ConsoleHandler

handler = ConsoleHandler(theme=SCIETEX_DARK)
```

In color mode the level abbreviation (bold + level color, with a background on
CRITICAL), the logger name, the message, and a dim timestamp are painted with
the theme's palette.

## Color resolution

`resolve_color(stream=None, *, color=None, force_color=None, no_color=None)`
decides whether color is emitted: it honors an explicit
`force_color`/`no_color`/`color` override, then the `FORCE_COLOR`/`NO_COLOR`
environment variables, and finally TTY detection on the stream. A `None` stream
never yields color from TTY detection, so redirected and file output stay plain
unless `force_color`/`FORCE_COLOR` overrides.

## Converting a Textual theme

`from_textual_theme(theme, *, name=None, color=True, ansi_palette=None,
critical_darken=0.12)` converts a Textual theme into a `LoggingTheme` so an
application already themed with Textual can reuse the same palette for its
console logs. The converter reads the theme duck-typed, so `scietex.logging`
carries no runtime Textual dependency:

```python
from textual.theme import Theme

from scietex.logging import ScietexFormatter, from_textual_theme

textual_theme = Theme(name="nord", primary="#88C0D0", background="#2E3440")
logging_theme = from_textual_theme(textual_theme)
formatter = ScietexFormatter(theme=logging_theme, color=True)
```

Base slots map directly from the Textual theme; derived slots are reproduced
with the ported CIE-Lab math. ANSI themes (`ansi-dark`/`ansi-light`) require an
explicit `ansi_palette` argument.

`from_textual_theme` defaults to `color=True`; pass `color=False` to build a
monochrome theme from a Textual theme. To preserve a `color=False` policy from
an existing `LoggingTheme`, use that theme directly instead of round-tripping
through the converter.

For a full worked example of keeping log colors in sync with a Textual app's
active theme, see {doc}`textual`.

## Custom palettes

Build a `Palette` and wrap it in a `LoggingTheme` to define your own colors. See
`examples/custom_palette.py` and `examples/scietex_color_theme.py` for runnable
examples, and {doc}`../api/themes` for the full API.
