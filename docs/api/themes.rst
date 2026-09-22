Themes and Color
================

The ``scietex.logging.theme`` module provides the color/theme API consumed by
``ScietexFormatter`` and ``ConsoleHandler``. Colors are emitted as 24-bit
truecolor SGR sequences to reproduce the exact brand palette (yellow
``#FFDB1C``, dark gray ``#31313B``, black ``#1F202A``). The module is a
stdlib-only neutral leaf, so formatters and handlers can depend on it without
an import cycle.

``Palette`` and ``LoggingTheme`` are frozen dataclasses; the concrete themes
(``MonochromeTheme``, ``ScietexLight``, ``ScietexDark``) are provided as the
singletons ``MONOCHROME``, ``SCIETEX_LIGHT``, and ``SCIETEX_DARK``.
``resolve_color`` decides whether ANSI color should be emitted for a given
stream.

.. autoclass:: scietex.logging.Palette
   :no-index:

.. autoclass:: scietex.logging.LoggingTheme
   :no-index:

.. autoclass:: scietex.logging.MonochromeTheme
   :no-index:

.. autoclass:: scietex.logging.ScietexLight
   :no-index:

.. autoclass:: scietex.logging.ScietexDark
   :no-index:

.. autofunction:: scietex.logging.resolve_color
   :no-index:

Textual interop
---------------

``from_textual_theme`` converts a Textual theme into a ``LoggingTheme`` without
a runtime Textual dependency: the theme is read duck-typed, base slots map
directly, and derived slots are reproduced with the ported CIE-Lab math. ANSI
themes require an explicit ``ansi_palette``.

.. autofunction:: scietex.logging.from_textual_theme
   :no-index:
