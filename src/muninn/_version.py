"""Single source of truth for the package version.

Kept in a tiny module so that ``pyproject.toml`` and import-time lookups stay
in sync; the build backend reads ``[project].version`` independently.
"""

__version__ = "0.1.0"
