"""Build feature_drift_monitoring.ipynb from typed source.

This generator exists so the notebook source lives in a single readable Python file
instead of hand-edited JSON. Run it any time the notebook content changes:

    python notebooks/_build_drift_notebook.py

It writes notebooks/feature_drift_monitoring.ipynb with deterministic cell IDs so
diffs are reviewable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

NB_PATH = Path(__file__).parent / "feature_drift_monitoring.ipynb"


def cell_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def md(seed: str, text: str) -> dict:
    return {
        "cell_type": "markdown",
        "id": cell_id(seed),
        "metadata": {},
        "source": _split_lines(text),
    }


def code(seed: str, text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell_id(seed),
        "metadata": {},
        "outputs": [],
        "source": _split_lines(text),
    }


def _split_lines(text: str) -> list[str]:
    """nbformat stores source as a list of lines, each ending in \\n except the last."""
    text = text.lstrip("\n").rstrip("\n")
    lines = text.split("\n")
    return [line + "\n" for line in lines[:-1]] + [lines[-1]]


CELLS = [
    md(
        "title",
        """
# Muninn Feature Drift Monitoring

Detect when a deployed feature pipeline is producing values that no longer match the
shape of the values it produced yesterday. Two complementary checks:

1. **Distributional drift.** Split a recent feature series into a baseline window and an
   observed window. Compute summary-statistic deltas (Δmean in units of baseline σ, σ-ratio,
   quantile shifts). A large delta is a candidate alert.
2. **Replay-divergence sanity.** Submit a replay over the same window and confirm
   `eventsReplayed` matches the live event count. Any mismatch is a determinism red flag
   that warrants a deeper look at the contract test
   (`tests/test_openapi_contract.py`) and the server's
   [DETERMINISTIC_REPLAY.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/DETERMINISTIC_REPLAY.md).

**Prerequisites.** A Muninn server on `localhost:8080` with at least one continuous-output
feature registered (e.g. `vpin` or `obi`). If a deploy has cycled `codeVersion` within the
monitoring window, the notebook will surface that — otherwise the version cohort cell will
report a single version.

**What this demo is not.** It is not a model-monitoring service, an alert router, or a
statistical-power-justified change detector. It is the SDK-side scaffolding a researcher
runs interactively before deciding what to wire into production.
""",
    ),
    code(
        "imports",
        """
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import seaborn as sns

from muninn import MuninnClient

sns.set_theme(style="whitegrid")
pl.Config.set_tbl_rows(8)

INSTRUMENT = "BTC-USDT"
FEATURE = "vpin"
START = "2026-05-10T14:00:00Z"
END = "2026-05-10T18:00:00Z"

# Threshold for "flag as drifted" — a ~2σ shift in mean, or σ-ratio outside [0.5, 2.0].
# Researcher judgement, not a calibrated p-value.
MEAN_SIGMA_THRESHOLD = 2.0
STD_RATIO_BAND = (0.5, 2.0)
""",
    ),
    md(
        "section-pull",
        """
## 1. Pull a single feature's recent history

We work on one feature for clarity. Multi-feature drift monitoring is the same loop wrapped
in a `for` — the SDK fetches in parallel via `get_features`.
""",
    ),
    code(
        "pull-feature",
        """
with MuninnClient() as muninn:
    series = muninn.get_feature(
        FEATURE,
        instrument=INSTRUMENT,
        start=START,
        end=END,
    )

print(f"rows={series.height}  span={series['event_time'].min()} → {series['event_time'].max()}")
series.head()
""",
    ),
    md(
        "section-split",
        """
## 2. Split into baseline and observed halves

Cut the window in half on `event_time`. The first half is what we call "normal" for this
demo; the second half is what we test against. In a real monitoring loop the baseline would
be a fixed rolling reference (e.g. last 30 days) and the observed window the current hour.
""",
    ),
    code(
        "split",
        """
df = series.to_pandas().sort_values("event_time").reset_index(drop=True)
df["value"] = df["value"].astype(float)

split_idx = len(df) // 2
baseline = df.iloc[:split_idx]
observed = df.iloc[split_idx:]

split_at = df["event_time"].iloc[split_idx]
print(f"split at {split_at}: baseline n={len(baseline)}, observed n={len(observed)}")
""",
    ),
    md(
        "section-summary",
        """
## 3. Summary statistics per half

Mean, standard deviation, and quantiles. The contract is intentionally narrow: distributions
of a stable feature should stay shaped roughly the same between adjacent windows of similar
length. A regime change in market microstructure (a liquidity event, a venue outage, an
exchange config push) typically shows up here first.
""",
    ),
    code(
        "summary",
        """
def summarize(name: str, x: pd.Series) -> dict:
    return {
        "window": name,
        "n": len(x),
        "mean": x.mean(),
        "std": x.std(ddof=1),
        "p05": x.quantile(0.05),
        "p50": x.quantile(0.50),
        "p95": x.quantile(0.95),
    }

summary = pd.DataFrame([summarize("baseline", baseline["value"]), summarize("observed", observed["value"])])
summary
""",
    ),
    md(
        "section-drift",
        """
## 4. Drift metrics

Three numbers per check:

- **Δmean (in baseline σ).** `(observed.mean - baseline.mean) / baseline.std`. Conventional
  alerting threshold sits around 2σ — but the right number depends on how often the feature
  is allowed to drift naturally.
- **σ-ratio.** `observed.std / baseline.std`. A regime that doubles or halves volatility is
  almost always worth a look.
- **Quantile shift.** `observed.p95 - baseline.p95`. The tails move first when a regime
  breaks, so the upper-quantile shift is often louder than the mean shift.
""",
    ),
    code(
        "drift-metrics",
        """
baseline_mean = baseline["value"].mean()
baseline_std = baseline["value"].std(ddof=1)
observed_mean = observed["value"].mean()
observed_std = observed["value"].std(ddof=1)

delta_mean_sigma = (observed_mean - baseline_mean) / baseline_std if baseline_std > 0 else np.nan
std_ratio = observed_std / baseline_std if baseline_std > 0 else np.nan
p95_shift = observed["value"].quantile(0.95) - baseline["value"].quantile(0.95)

flagged_mean = abs(delta_mean_sigma) >= MEAN_SIGMA_THRESHOLD
flagged_std = not (STD_RATIO_BAND[0] <= std_ratio <= STD_RATIO_BAND[1])

report = pd.DataFrame(
    [
        {"metric": "Δmean / baseline σ", "value": delta_mean_sigma, "flagged": flagged_mean},
        {"metric": "σ-ratio (observed / baseline)", "value": std_ratio, "flagged": flagged_std},
        {"metric": "p95 shift (raw units)", "value": p95_shift, "flagged": None},
    ]
)
report
""",
    ),
    md(
        "section-plot",
        """
## 5. Visualize: distribution overlay and time series

A side-by-side: the kernel density estimate of each half on the left, the raw value-over-time
with the split marked on the right. Eyes catch regime shifts that summary statistics
sometimes smooth over.
""",
    ),
    code(
        "plot",
        """
fig, axes = plt.subplots(1, 2, figsize=(13, 4))

sns.kdeplot(baseline["value"], ax=axes[0], label="baseline", fill=True, alpha=0.4)
sns.kdeplot(observed["value"], ax=axes[0], label="observed", fill=True, alpha=0.4)
axes[0].set_title(f"{FEATURE} — distribution by window")
axes[0].set_xlabel(FEATURE)
axes[0].legend()

axes[1].plot(df["event_time"], df["value"], lw=0.8, color="steelblue")
axes[1].axvline(split_at, color="crimson", lw=1.0, linestyle="--", label="split")
axes[1].set_title(f"{FEATURE} — value over time")
axes[1].set_xlabel("event_time")
axes[1].legend()

fig.tight_layout()
""",
    ),
    md(
        "section-versions",
        """
## 6. Code-version cohorts

Every `FeatureValue` carries the `code_version` that produced it. A deploy mid-window will
split the series across versions. If two cohorts overlap in `event_time` — which can happen
during a rolling restart — that's a hint to look harder, because the two versions are
producing values for the same logical window.
""",
    ),
    code(
        "versions",
        """
version_counts = (
    series
    .group_by("code_version")
    .agg(
        pl.len().alias("n"),
        pl.col("event_time").min().alias("first_seen"),
        pl.col("event_time").max().alias("last_seen"),
    )
    .sort("first_seen")
)
print(f"distinct code_versions in window: {version_counts.height}")
version_counts
""",
    ),
    md(
        "section-replay",
        """
## 7. Replay-divergence sanity

Submit a replay over the same window. When it completes, `eventsReplayed` is the count of
input events the server re-processed. There is no SDK assertion against the live values here
— in practice the divergence check happens server-side via the
[OpenAPI contract test](https://github.com/lgreene03/muninn-py/blob/main/tests/test_openapi_contract.py)
and the determinism property documented in DETERMINISTIC_REPLAY.md. What the notebook *can*
do is surface anomalous replay timings: a job that takes orders of magnitude longer than the
window's live ingestion suggests something has changed in the input topology.
""",
    ),
    code(
        "replay",
        """
import time

with MuninnClient() as muninn:
    job = muninn.submit_replay_job(start=START, end=END, feature_version="v1")
    print(f"submitted job_id={job.job_id} status={job.status}")

    while not job.is_terminal:
        time.sleep(2)
        job = muninn.get_replay_job(job.job_id)

    print(f"final status     : {job.status}")
    print(f"events replayed  : {job.events_replayed}")
    print(f"elapsed          : {job.elapsed}")

    # Sanity ratio: ms per replayed event. A baseline number for this server.
    if job.elapsed is not None and job.events_replayed > 0:
        ms_per_event = job.elapsed.total_seconds() * 1000 / job.events_replayed
        print(f"throughput       : {ms_per_event:.3f} ms/event")
""",
    ),
    md(
        "section-next",
        """
## Next steps

- **Wire to alerting.** Swap the `print` calls in cells 4 and 7 for a writer to wherever your
  team reads alerts. The values above are JSON-serialisable already.
- **Move the baseline.** Replace the "split in half" baseline with a fixed reference window
  (yesterday's same hour, last week's same day) for steadier comparisons.
- **Extend to a panel.** Loop over `muninn.list_features()` and run the same checks per
  feature. The SDK fans out the GETs in parallel; runtime stays roughly constant.
- **Promote to a script.** Same SDK calls in a cron-driven Python script give you a daily
  drift report without a notebook in the loop. See `docs/RELEASING.md` on the SDK side and
  the server's `DETERMINISTIC_REPLAY.md` for the contract this notebook is verifying.
""",
    ),
]


def build() -> dict:
    return {
        "cells": CELLS,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "codemirror_mode": {"name": "ipython", "version": 3},
                "file_extension": ".py",
                "mimetype": "text/x-python",
                "name": "python",
                "nbconvert_exporter": "python",
                "pygments_lexer": "ipython3",
                "version": "3.11",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    NB_PATH.write_text(json.dumps(build(), indent=1) + "\n")
    print(f"wrote {NB_PATH}")
