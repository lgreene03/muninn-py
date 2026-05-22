"""Command-line interface for the muninn SDK.

Installed as ``muninn`` (see ``[project.scripts]`` in ``pyproject.toml``).

Provides shell access to the most common operations the SDK supports.
Output is JSON by default — composable with ``jq`` and shell pipelines —
with a ``--format table`` option for human-readable display.

Examples
--------
.. code-block:: shell

    muninn features list
    muninn features get vwap.1m --instrument BTC-USDT \\
        --start 2026-05-10T14:00:00Z --end 2026-05-10T15:00:00Z
    muninn replay submit --start 2026-05-10T14:00:00Z --end 2026-05-10T15:00:00Z
    muninn replay status 019e1e50-0000-7000-9ccc-000000000001

The CLI deliberately does not depend on the notebook helpers — those are
DataFrame-shaped and don't belong on a shell. For richer analysis, use
the SDK from Python.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
import polars as pl

from muninn import MuninnClient, __version__
from muninn.exceptions import MuninnAPIError, MuninnError, MuninnTimeoutError


def _print_output(payload: Any, fmt: str) -> None:
    if fmt == "json":
        click.echo(json.dumps(payload, indent=2, default=str, sort_keys=True))
        return

    # table
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        df = pl.DataFrame(payload)
        click.echo(str(df))
        return
    if isinstance(payload, dict):
        for key in sorted(payload.keys()):
            click.echo(f"{key}: {payload[key]}")
        return
    click.echo(str(payload))


def _df_records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return df.to_dicts()


@click.group()
@click.version_option(version=__version__, prog_name="muninn")
@click.option(
    "--host",
    envvar="MUNINN_HOST",
    default="http://localhost:8080",
    show_default=True,
    help="Base URL of the Muninn server. Reads MUNINN_HOST if set.",
)
@click.option(
    "--timeout",
    type=float,
    default=30.0,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "table"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def cli(ctx: click.Context, host: str, timeout: float, fmt: str) -> None:
    """muninn — shell access to the Muninn feature platform."""
    ctx.ensure_object(dict)
    ctx.obj["host"] = host
    ctx.obj["timeout"] = timeout
    ctx.obj["format"] = fmt.lower()


# ----- features -------------------------------------------------------------


@cli.group()
def features() -> None:
    """Feature discovery and time-series queries."""


@features.command("list")
@click.pass_context
def features_list(ctx: click.Context) -> None:
    """List all feature definitions registered on the server."""
    with MuninnClient(host=ctx.obj["host"], timeout=ctx.obj["timeout"]) as client:
        defs = client.list_features()
    _print_output([d.model_dump(mode="json") for d in defs], ctx.obj["format"])


@features.command("get")
@click.argument("name")
@click.option("--instrument", required=True, help="Instrument symbol, e.g. BTC-USDT.")
@click.option("--start", required=True, help="ISO-8601 inclusive start.")
@click.option("--end", required=True, help="ISO-8601 exclusive end.")
@click.option("--limit", type=int, default=None, help="Cap on rows returned.")
@click.pass_context
def features_get(
    ctx: click.Context,
    name: str,
    instrument: str,
    start: str,
    end: str,
    limit: int | None,
) -> None:
    """Fetch one feature's time-series for an instrument."""
    with MuninnClient(host=ctx.obj["host"], timeout=ctx.obj["timeout"]) as client:
        df = client.get_feature(
            name, instrument=instrument, start=start, end=end, limit=limit
        )
    _print_output(_df_records(df), ctx.obj["format"])


# ----- replay ---------------------------------------------------------------


@cli.group()
def replay() -> None:
    """Replay job submission and inspection."""


@replay.command("submit")
@click.option("--start", required=True, help="ISO-8601 inclusive start of the replay range.")
@click.option("--end", required=True, help="ISO-8601 exclusive end of the replay range.")
@click.option(
    "--topic",
    "topics",
    multiple=True,
    default=None,
    help="Topic(s) to replay from. May be passed multiple times.",
)
@click.option(
    "--feature-version",
    default=None,
    help="Feature version to produce outputs for.",
)
@click.pass_context
def replay_submit(
    ctx: click.Context,
    start: str,
    end: str,
    topics: tuple[str, ...],
    feature_version: str | None,
) -> None:
    """Submit a new replay job; returns the initial PENDING state."""
    with MuninnClient(host=ctx.obj["host"], timeout=ctx.obj["timeout"]) as client:
        job = client.submit_replay_job(
            start=start,
            end=end,
            topics=list(topics) if topics else None,
            feature_version=feature_version,
        )
    _print_output(job.model_dump(mode="json"), ctx.obj["format"])


@replay.command("status")
@click.argument("job_id")
@click.pass_context
def replay_status(ctx: click.Context, job_id: str) -> None:
    """Poll a single replay job's status."""
    with MuninnClient(host=ctx.obj["host"], timeout=ctx.obj["timeout"]) as client:
        job = client.get_replay_job(job_id)
    _print_output(job.model_dump(mode="json"), ctx.obj["format"])


@replay.command("list")
@click.pass_context
def replay_list(ctx: click.Context) -> None:
    """List every replay job the server is tracking."""
    with MuninnClient(host=ctx.obj["host"], timeout=ctx.obj["timeout"]) as client:
        jobs = client.list_replay_jobs()
    _print_output([j.model_dump(mode="json") for j in jobs], ctx.obj["format"])


@cli.command()
def dashboard() -> None:
    """Launch the Streamlit researcher dashboard.

    Requires the [dashboard] extra:

        pip install 'muninn-py[dashboard]'

    Opens a browser-based UI for feature exploration, forward returns +
    IC analysis, and calibration-CSV viewing. See ``muninn.dashboard``
    package docs for the page layout.
    """
    try:
        from muninn.dashboard import main as _dashboard_main
    except ImportError as exc:  # pragma: no cover - import-time error path
        raise click.ClickException(
            "muninn[dashboard] is not installed.\n"
            "Install with:  pip install 'muninn-py[dashboard]'"
        ) from exc
    _dashboard_main()


# ----- entry point ----------------------------------------------------------


def main() -> None:
    """Entry point used by ``[project.scripts] muninn``."""
    try:
        cli(obj={}, standalone_mode=True)
    except MuninnAPIError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    except MuninnTimeoutError as exc:
        click.echo(f"timeout: {exc}", err=True)
        sys.exit(3)
    except MuninnError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
