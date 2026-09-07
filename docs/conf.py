"""Sphinx configuration for the scietex.logging documentation."""

import os
import sys

# Make the package importable from src/ regardless of install state, so
# autodoc can resolve the public API even when the package is not pip-installed.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

project = "scietex.logging"
copyright = "2026, Anton Bondarenko"
author = "Anton Bondarenko"

# Version/release are read from the package's single source of truth.
import scietex.logging as _pkg  # noqa: E402

version = _pkg.__version__
release = _pkg.__version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.autosummary",
]

# MyST: parse .md files as MyST Markdown.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# MyST label targets like (formatter-scope)= are used for cross-doc section refs.
myst_enable_extensions = [
    "colon_fence",
]

# autodoc: document members by default; :no-index: on each directive avoids the
# duplicate-description warnings that arise when base and subclass are both
# documented in one build.
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

# napoleon: the package docstrings use Google-style Args:/Returns:/Attributes:.
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# intersphinx: link to the Python stdlib docs for logging/asyncio references.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
