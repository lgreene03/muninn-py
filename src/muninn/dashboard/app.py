"""Streamlit entrypoint for the muninn researcher dashboard.

Run via:

    muninn dashboard
    # or
    streamlit run -m muninn.dashboard.app
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from muninn import MuninnClient
from muninn.exceptions import MuninnAPIError, MuninnError
from muninn.notebook import (
    forward_returns,
    hit_rate,
    information_coefficient,
)


# ─── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Muninn — researcher dashboard",
    page_icon="🦅",
    layout="wide",
)

DEFAULT_HOST = os.environ.get("MUNINN_HOST", "http://localhost:8090")


# ─── Sidebar: connection + page selection ───────────────────────────────────

with st.sidebar:
    st.header("🦅 Muninn")
    st.caption("Researcher dashboard — Direction A")

    host = st.text_input(
        "Muninn host",
        value=DEFAULT_HOST,
        help="HTTP base URL of the muninn server. "
        "Defaults to MUNINN_HOST env var or http://localhost:8090.",
    )
    page = st.radio(
        "Page",
        options=("Feature explorer", "Forward returns & IC", "Calibration viewer"),
        label_visibility="collapsed",
    )

    st.divider()
    st.caption(
        "This is a sanity surface, not a research framework. "
        "For real notebooks see `muninn-py/notebooks/`."
    )


@st.cache_resource(show_spinner=False)
def _make_client(host: str) -> MuninnClient:
    """One MuninnClient per host — Streamlit caches across reruns."""
    return MuninnClient(host=host)


# ─── Page 1 — Feature explorer ──────────────────────────────────────────────


def _feature_explorer(client: MuninnClient) -> None:
    st.title("Feature explorer")
    st.caption(
        "Pull a multi-feature panel for one instrument and inspect it. "
        "Read-only — no mutations against the server."
    )

    try:
        features = client.list_features()
    except (MuninnAPIError, MuninnError) as e:
        st.error(f"Couldn't list features: {e}")
        st.info(
            "Double-check the host in the sidebar. The default "
            "`http://localhost:8090` assumes a local docker-compose-up "
            "muninn server."
        )
        return

    feature_names = sorted({f.feature_name for f in features})
    if not feature_names:
        st.warning("Server returned no feature definitions.")
        return

    col1, col2 = st.columns([1, 1])
    with col1:
        instrument = st.text_input("Instrument", value="BTC-USDT")
    with col2:
        picked = st.multiselect(
            "Features",
            options=feature_names,
            default=feature_names[: min(3, len(feature_names))],
        )

    col3, col4 = st.columns(2)
    with col3:
        end = st.datetime_input(
            "End time (UTC)",
            value=datetime.now(timezone.utc),
        ) if hasattr(st, "datetime_input") else st.text_input(
            "End time (ISO, UTC)",
            value=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    with col4:
        lookback_hours = st.slider("Lookback (hours)", 1, 168, 24)

    end_dt = end if isinstance(end, datetime) else datetime.fromisoformat(
        str(end).replace("Z", "+00:00")
    )
    start_dt = end_dt - timedelta(hours=lookback_hours)

    if not picked:
        st.info("Select at least one feature on the right to load a panel.")
        return

    with st.spinner(f"Loading {len(picked)} features…"):
        try:
            df = client.get_features(
                instrument=instrument,
                features=picked,
                start=start_dt,
                end=end_dt,
            )
        except (MuninnAPIError, MuninnError) as e:
            st.error(f"Fetch failed: {e}")
            return

    if df.is_empty():
        st.warning("Server returned no rows for that window.")
        return

    st.success(f"Loaded **{len(df):,}** rows across {len(picked)} features.")

    # Quick freshness diagnostic — single most useful number when something
    # looks wrong.
    if "event_time" in df.columns:
        latest = df.select(pl.col("event_time").max()).item()
        if latest is not None:
            age = datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)
            st.metric("Latest event age", f"{age.total_seconds():.0f}s")

    tab_chart, tab_table, tab_stats = st.tabs(["Chart", "Rows", "Summary"])
    with tab_chart:
        value_cols = [c for c in df.columns if c not in ("event_time", "instrument")]
        if value_cols:
            melted = df.unpivot(
                index="event_time",
                on=value_cols,
                variable_name="feature",
                value_name="value",
            ).drop_nulls()
            fig = px.line(
                melted.to_pandas(),
                x="event_time",
                y="value",
                color="feature",
                title=f"{instrument} — {lookback_hours}h",
            )
            fig.update_layout(height=400, hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
    with tab_table:
        st.dataframe(df.tail(500).to_pandas(), use_container_width=True, height=400)
    with tab_stats:
        st.dataframe(
            df.select(pl.exclude("event_time", "instrument")).describe().to_pandas(),
            use_container_width=True,
        )


# ─── Page 2 — Forward returns & IC ──────────────────────────────────────────


def _forward_returns_page(client: MuninnClient) -> None:
    st.title("Forward returns & IC")
    st.caption(
        "Compute n-step-ahead returns from a price feature and the "
        "Spearman IC of every signal against the resulting return column. "
        "Toy by design — real research adds rolling windows + winsorization."
    )

    try:
        features = client.list_features()
    except MuninnError as e:
        st.error(f"Couldn't list features: {e}")
        return

    feature_names = sorted({f.feature_name for f in features})
    if not feature_names:
        st.warning("No features available on this server.")
        return

    col1, col2 = st.columns([1, 1])
    with col1:
        instrument = st.text_input("Instrument", value="BTC-USDT")
    with col2:
        lookback_hours = st.slider("Lookback (hours)", 1, 168, 24)

    price_default = next(
        (f for f in feature_names if "vwap" in f.lower() or "price" in f.lower()),
        feature_names[0],
    )
    col3, col4 = st.columns([1, 2])
    with col3:
        price_col = st.selectbox("Price column", options=feature_names, index=feature_names.index(price_default))
    with col4:
        signals = st.multiselect(
            "Signal columns",
            options=[f for f in feature_names if f != price_col],
            default=[f for f in feature_names if f != price_col][:3],
        )

    periods_str = st.text_input("Forward periods (comma-separated)", value="1,5,15")
    try:
        periods = [int(p.strip()) for p in periods_str.split(",") if p.strip()]
    except ValueError:
        st.error("Periods must be a comma-separated list of integers.")
        return

    if not signals or not periods:
        st.info("Pick at least one signal and one period to compute.")
        return

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(hours=lookback_hours)

    with st.spinner("Loading panel and computing IC…"):
        try:
            df = client.get_features(
                instrument=instrument,
                features=[price_col, *signals],
                start=start_dt,
                end=end_dt,
            )
        except MuninnError as e:
            st.error(f"Fetch failed: {e}")
            return

    if df.is_empty():
        st.warning("No rows returned for that window.")
        return

    df = forward_returns(df, price_col=price_col, periods=periods, log=True)

    # IC matrix: rows = signals, cols = forward periods.
    ic_rows = []
    for n in periods:
        ic_df = information_coefficient(
            df,
            signals=signals,
            return_col=f"fwd_return_{n}",
            method="spearman",
        )
        for r in ic_df.iter_rows(named=True):
            ic_rows.append({"signal": r["signal"], "period": n, "ic": r["ic"]})

    ic_pivot = pl.DataFrame(ic_rows).pivot(
        index="signal", on="period", values="ic"
    )

    st.subheader("Information coefficient (Spearman)")
    st.dataframe(ic_pivot.to_pandas(), use_container_width=True)

    # Plotly heatmap.
    heat = ic_pivot.to_pandas().set_index("signal")
    fig = go.Figure(
        go.Heatmap(
            z=heat.values,
            x=[f"fwd_{p}" for p in heat.columns],
            y=heat.index,
            colorscale="RdBu",
            zmid=0,
            colorbar={"title": "IC"},
        )
    )
    fig.update_layout(height=400, title="IC by signal × forward period")
    st.plotly_chart(fig, use_container_width=True)

    # Hit-rate as a one-row diagnostic.
    with st.expander("Hit-rate (signal > 0 ↔ forward return > 0)"):
        hits = {
            sig: hit_rate(df, signal=sig, return_col=f"fwd_return_{periods[0]}")
            for sig in signals
        }
        st.dataframe(
            pl.DataFrame({"signal": list(hits.keys()), "hit_rate": list(hits.values())}).to_pandas(),
            use_container_width=True,
        )


# ─── Page 3 — Calibration viewer ────────────────────────────────────────────


def _calibration_viewer() -> None:
    st.title("Calibration viewer")
    st.caption(
        "Upload or point to a CSV produced by huginn's `cmd/calibrate`. "
        "Renders Sharpe and hit-rate heatmaps by parameter combination."
    )

    upload = st.file_uploader(
        "Calibration CSV",
        type=["csv"],
        help="Output of `muninn-calibrate --strategy ... --out ...csv`",
    )
    path_input = st.text_input(
        "…or path on disk",
        value="",
        help="Absolute path. Useful when streamlit runs on the same machine "
        "as huginn.",
    )

    df = None
    if upload is not None:
        df = pl.read_csv(upload)
    elif path_input.strip():
        p = Path(path_input).expanduser()
        if not p.exists():
            st.error(f"No file at {p}")
            return
        df = pl.read_csv(p)

    if df is None:
        st.info("Drop a CSV or paste a path above.")
        return

    if df.is_empty():
        st.warning("File loaded but contained no rows.")
        return

    st.success(f"Loaded **{len(df):,}** parameter combinations.")
    st.dataframe(df.to_pandas(), use_container_width=True, height=300)

    # Sharpe heatmap requires exactly two varying parameter columns.
    metric_cols = {
        "sharpe",
        "max_drawdown",
        "fills",
        "realized_pnl",
        "hit_rate",
        "turnover",
        "avg_hold_seconds",
    }
    param_cols = [c for c in df.columns if c not in metric_cols and c != "strategy"]

    if len(param_cols) < 2:
        st.info(
            "Heatmap requires ≥ 2 parameter columns in the grid. "
            "This CSV varies on: " + ", ".join(param_cols) + "."
        )
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        x_axis = st.selectbox("X axis", options=param_cols, index=0)
    with col2:
        y_axis = st.selectbox("Y axis", options=param_cols, index=1)
    with col3:
        metric = st.selectbox(
            "Metric", options=sorted(metric_cols & set(df.columns)), index=0
        )

    if x_axis == y_axis:
        st.warning("Pick distinct X and Y axes for the heatmap.")
        return

    pivot = df.pivot(index=y_axis, on=x_axis, values=metric, aggregate_function="first")
    pdf = pivot.to_pandas().set_index(y_axis)

    fig = go.Figure(
        go.Heatmap(
            z=pdf.values,
            x=pdf.columns.astype(str),
            y=pdf.index.astype(str),
            colorscale="Viridis",
            colorbar={"title": metric},
        )
    )
    fig.update_layout(
        height=500,
        title=f"{metric} by {x_axis} × {y_axis}",
        xaxis_title=x_axis,
        yaxis_title=y_axis,
    )
    st.plotly_chart(fig, use_container_width=True)


# ─── Router ─────────────────────────────────────────────────────────────────


def _run() -> None:
    if page == "Calibration viewer":
        # No server connection needed for this page.
        _calibration_viewer()
        return

    client = _make_client(host)
    if page == "Feature explorer":
        _feature_explorer(client)
    elif page == "Forward returns & IC":
        _forward_returns_page(client)


_run()
