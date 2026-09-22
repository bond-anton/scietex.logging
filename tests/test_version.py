"""Test that the package version is well-formed and single-sourced."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no stdlib tomllib
    import tomli as tomllib

from scietex.logging import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_is_a_nonzero_dotted_triple():
    """__version__ is a stripped 'N.N.N' string whose parts are non-negative ints."""
    assert isinstance(__version__, str)
    assert __version__ == __version__.strip()

    parts = __version__.split(".")
    assert len(parts) == 3
    major, minor, patch = (int(part) for part in parts)
    assert all(part >= 0 for part in (major, minor, patch))
    assert major + minor + patch > 0


def test_version_sources_from_package_attribute():
    """pyproject.toml declares __version__ as the dynamic version source."""
    with PYPROJECT.open("rb") as f:
        pyproject = tomllib.load(f)

    assert "version" in pyproject["project"]["dynamic"]
    assert (
        pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        == "scietex.logging.__version__"
    )
