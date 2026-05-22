"""Streamlit researcher dashboard over the muninn-py SDK.

A polished UI surface for quants and PMs to explore Muninn features
without writing notebook code. Direction A of the four-repo product
plan — see ``docs/ROADMAP.md`` Phase E.

The dashboard lives behind the optional ``[dashboard]`` extra so a
plain ``pip install muninn-py`` stays lean::

    pip install 'muninn-py[dashboard]'
    muninn dashboard

Pages:

* **Feature explorer** — pick instrument + features, see time-series
  panel, summary stats, freshness diagnostics.
* **Forward returns & IC** — compute forward returns from any price
  column, render information-coefficient bar charts per signal.
* **Calibration viewer** — upload a CSV produced by huginn's
  ``cmd/calibrate``, render Sharpe / hit-rate heatmaps by parameter.

Streamlit is the right pick here because:

- One file per page, hot reload, no build step.
- Reads the SDK directly — no separate API to maintain.
- The audience is researchers, not browser developers.

Notably *not* included: authentication, multi-tenancy, billing. Those
belong in a customer-facing build (Direction C) which is a different
product entirely.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """Launch the Streamlit app. Importable so ``muninn dashboard`` and
    direct ``streamlit run muninn/dashboard/app.py`` both work."""
    import os
    import subprocess
    import sys
    from importlib.resources import files

    app_path = files("muninn.dashboard").joinpath("app.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        # Server defaults that play nicely with the four-repo stack's
        # 8081/8083 layout. Override via env or CLI.
        "--server.address",
        os.environ.get("MUNINN_DASHBOARD_HOST", "127.0.0.1"),
        "--server.port",
        os.environ.get("MUNINN_DASHBOARD_PORT", "8501"),
    ]
    subprocess.run(cmd, check=True)
