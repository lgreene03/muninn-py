"""Alpha-research diagnostics — IC, alpha decay, and capacity analysis.

This module is the *signal-evaluation* half of the Muninn research SDK. Where
:mod:`muninn.notebook` gets you from a feature panel to an alpha score and
:mod:`muninn.factor` turns scores into tradeable weights, this module answers
the questions a quant asks **before** committing capital to a signal:

``information_coefficient`` / ``ic_decay_curve``
    How well does the alpha predict forward returns, and at what horizon does
    that predictive power live? Rank (Spearman) or Pearson IC at multiple
    horizons, each with a t-statistic and a directional hit-rate, plus the
    *decay curve* of IC as the forward horizon grows.

``signal_half_life`` / ``autocorrelation``
    How fast does the signal itself mean-revert? An AR(1) half-life derived
    from the lag-1 autocorrelation tells you how stale a stored score becomes
    and therefore how often you must refresh / re-trade it.

``capacity_estimate``
    Given a square-root market-impact model and a tolerance for how much
    Sharpe you are willing to give up to impact, how much notional can you
    deploy before the impact eats the edge? A parameterised, closed-form
    estimate that demonstrates the capacity concept.

Design notes
------------
- **numpy / pandas only.** No scipy, no sklearn — t-distribution tail
  probabilities use a numerically-stable continued-fraction incomplete-beta,
  so significance testing works on the stdlib-plus-numpy floor.
- **Self-contained.** Nothing here imports the HTTP client; every function is
  a pure transformation of arrays / Series and is unit-testable offline.
- **Complements, does not replace,** :func:`muninn.notebook.information_coefficient`
  (a one-line Polars rank-IC). This module is the deeper, multi-horizon,
  significance-aware version that the worked research example wires together.

Conventions
-----------
- A *signal* and *forward returns* are 1-D, time-ordered, equal-length series.
- ``np.nan`` in either series at a given index drops that pair pairwise.
- Returns are simple per-period returns unless noted; horizons are in *bars*.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

__all__ = [
    "ICResult",
    "CapacityResult",
    "autocorrelation",
    "capacity_estimate",
    "forward_returns",
    "ic",
    "ic_decay_curve",
    "rank_ic",
    "signal_half_life",
]

FloatArray = npt.NDArray[np.float64]


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _aligned(a: npt.ArrayLike, b: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    """Return the two series as float arrays with any nan-containing pair dropped."""
    x = np.asarray(a, dtype=float).ravel()
    y = np.asarray(b, dtype=float).ravel()
    if x.shape != y.shape:
        raise ValueError(f"series length mismatch: {x.shape} vs {y.shape}")
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _rankdata(v: FloatArray) -> FloatArray:
    """Average-rank transform (ties share the mean of their rank span).

    A small, dependency-free stand-in for ``scipy.stats.rankdata`` so the
    Spearman IC matches the textbook tie-corrected definition.
    """
    n = v.size
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # Resolve ties by averaging ranks within each group of equal values.
    sorted_v = v[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sorted_v[j] == sorted_v[i]:
            j += 1
        if j - i > 1:
            avg = ranks[order[i:j]].mean()
            ranks[order[i:j]] = avg
        i = j
    return ranks


def _pearson(x: FloatArray, y: FloatArray) -> float:
    """Pearson correlation, returning ``nan`` for a degenerate (flat) input."""
    if x.size < 2:
        return float("nan")
    xc = x - x.mean()
    yc = y - y.mean()
    denom = np.sqrt(float(xc @ xc) * float(yc @ yc))
    if denom == 0.0:
        return float("nan")
    return float((xc @ yc) / denom)


def _student_t_two_sided_p(t: float, dof: float) -> float:
    """Two-sided tail probability ``P(|T| > |t|)`` for Student-t(dof).

    This is the two-sided p-value, **not** a one-sided survival function:
    it integrates both tails of the distribution. Implemented via the
    regularised incomplete beta function ``I_x(dof/2, 1/2)`` with
    ``x = dof / (dof + t^2)``; exact and needs only numpy. For large ``dof``
    it converges to the two-sided normal tail, as expected.
    """
    if not np.isfinite(t) or dof <= 0:
        return float("nan")
    x = dof / (dof + t * t)
    return float(_betainc_reg(dof / 2.0, 0.5, x))


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta ``I_x(a, b)`` via Lentz's continued fraction.

    Mirrors Numerical Recipes' ``betai``/``betacf``. Used only for t-test
    p-values, so the standard tolerance (1e-10) is ample.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = _lgamma(a + b) - _lgamma(a) - _lgamma(b)
    front = np.exp(lbeta + a * np.log(x) + b * np.log(1.0 - x))
    # Use the symmetry relation for faster CF convergence when x is large.
    if x < (a + 1.0) / (a + b + 2.0):
        return float(front * _betacf(a, b, x) / a)
    return float(1.0 - front * _betacf(b, a, 1.0 - x) / b)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Lentz's algorithm)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _lgamma(z: float) -> float:
    """Log-gamma via the Lanczos approximation (g=7, n=9). Pure float math."""
    g = 7
    coef = [
        0.99999999999980993,
        676.5203681218851,
        -1259.1392167224028,
        771.32342877765313,
        -176.61502916214059,
        12.507343278686905,
        -0.13857109526572012,
        9.9843695780195716e-6,
        1.5056327351493116e-7,
    ]
    if z < 0.5:
        # Reflection formula.
        return float(
            np.log(np.pi / np.sin(np.pi * z)) - _lgamma(1.0 - z)
        )
    z -= 1.0
    a = coef[0]
    t = z + g + 0.5
    for i in range(1, g + 2):
        a += coef[i] / (z + i)
    return float(0.5 * np.log(2.0 * np.pi) + (z + 0.5) * np.log(t) - t + np.log(a))


# --------------------------------------------------------------------------- #
# Forward returns
# --------------------------------------------------------------------------- #
def forward_returns(
    prices: npt.ArrayLike,
    *,
    horizon: int = 1,
    log: bool = False,
) -> FloatArray:
    """N-bar forward return aligned to the *current* bar.

    ``out[t] = price[t+horizon] / price[t] - 1`` (or the log thereof). The last
    ``horizon`` entries are ``nan`` because their forward observation is past
    the end of the series. This is the numpy sibling of
    :func:`muninn.notebook.forward_returns` (which operates on a Polars frame).

    Parameters
    ----------
    prices:
        1-D price series, time-ordered ascending.
    horizon:
        Number of bars ahead. Must be a positive integer.
    log:
        If ``True`` return ``log(p_{t+h}/p_t)`` instead of the simple return.
    """
    if horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    p = np.asarray(prices, dtype=float).ravel()
    out = np.full(p.shape, np.nan)
    if p.size > horizon:
        future = p[horizon:]
        base = p[:-horizon]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = future / base
            out[:-horizon] = np.log(ratio) if log else ratio - 1.0
    return out


# --------------------------------------------------------------------------- #
# Information coefficient
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ICResult:
    """One horizon's information-coefficient diagnostics.

    Attributes
    ----------
    horizon:
        Forward horizon in bars.
    ic:
        The correlation between the signal and the forward return
        (rank/Spearman or Pearson per the call).
    t_stat:
        IC t-statistic ``ic * sqrt((n - 2) / (1 - ic^2))`` under the
        null of zero correlation.
    p_value:
        Two-sided p-value of ``t_stat`` against Student-t with ``n - 2`` dof.
    hit_rate:
        Fraction of observations where ``sign(signal) == sign(return)``
        (directional agreement), over the non-nan, non-zero-signal pairs.
    n:
        Number of aligned (non-nan) observations used.
    method:
        ``"spearman"`` or ``"pearson"``.
    """

    horizon: int
    ic: float
    t_stat: float
    p_value: float
    hit_rate: float
    n: int
    method: str


def ic(
    signal: npt.ArrayLike,
    forward_return: npt.ArrayLike,
    *,
    method: str = "spearman",
    horizon: int = 1,
) -> ICResult:
    """Information coefficient of a signal vs a forward-return series.

    Computes the (rank or linear) correlation plus its significance and a
    directional hit-rate, packaged in an :class:`ICResult`. The sign of the IC
    is the sign of the signal's predictive relationship: a signal that is high
    before up-moves has a positive IC.

    Parameters
    ----------
    signal:
        Alpha score per bar (1-D, time-ordered).
    forward_return:
        Forward return per bar, same length and alignment as ``signal``
        (e.g. from :func:`forward_returns`).
    method:
        ``"spearman"`` (default, rank IC — robust to outliers) or
        ``"pearson"`` (linear IC).
    horizon:
        Recorded on the result for bookkeeping; does not change the maths
        (the caller is responsible for passing a forward return of that
        horizon).
    """
    if method not in ("spearman", "pearson"):
        raise ValueError("method must be 'spearman' or 'pearson'")
    s, r = _aligned(signal, forward_return)
    n = int(s.size)
    if n < 3:
        return ICResult(horizon, float("nan"), float("nan"), float("nan"), float("nan"), n, method)

    coef = (
        _pearson(_rankdata(s), _rankdata(r))
        if method == "spearman"
        else _pearson(s, r)
    )

    if not np.isfinite(coef) or abs(coef) >= 1.0:
        t_stat = float("inf") if np.isfinite(coef) and abs(coef) >= 1.0 else float("nan")
        p_value = 0.0 if np.isfinite(t_stat) else float("nan")
    else:
        dof = n - 2
        t_stat = coef * np.sqrt(dof / (1.0 - coef * coef))
        p_value = _student_t_two_sided_p(t_stat, dof)

    # Directional hit-rate over non-zero signals.
    nz = s != 0.0
    hits = np.sign(s[nz]) == np.sign(r[nz])
    hit = float(hits.mean()) if nz.any() else float("nan")

    return ICResult(horizon, float(coef), float(t_stat), float(p_value), hit, n, method)


def rank_ic(signal: npt.ArrayLike, forward_return: npt.ArrayLike, *, horizon: int = 1) -> float:
    """Convenience: the Spearman (rank) IC value alone."""
    return ic(signal, forward_return, method="spearman", horizon=horizon).ic


def ic_decay_curve(
    prices: npt.ArrayLike,
    signal: npt.ArrayLike,
    *,
    horizons: Iterable[int] = (1, 5, 15),
    method: str = "spearman",
    log_returns: bool = False,
) -> pd.DataFrame:
    """IC at a range of forward horizons — the *alpha-decay* curve.

    For each horizon ``h`` it computes the forward return over ``h`` bars from
    ``prices`` (via :func:`forward_returns`) and the IC of ``signal`` against
    it. The result tabulates how predictive power changes as the horizon grows:
    a fast-decaying microstructure alpha peaks at ``h=1`` and fades, while a
    slower factor signal may strengthen out to longer horizons.

    Parameters
    ----------
    prices:
        Price series used to build forward returns at each horizon.
    signal:
        Alpha score per bar, aligned to ``prices``.
    horizons:
        Iterable of positive bar horizons to evaluate.
    method:
        ``"spearman"`` or ``"pearson"`` (passed to :func:`ic`).
    log_returns:
        Use log forward returns if ``True``.

    Returns
    -------
    pandas.DataFrame
        One row per horizon with columns
        ``["horizon", "ic", "t_stat", "p_value", "hit_rate", "n"]``,
        sorted ascending by horizon.
    """
    p = np.asarray(prices, dtype=float).ravel()
    s = np.asarray(signal, dtype=float).ravel()
    if p.shape != s.shape:
        raise ValueError(f"prices and signal length mismatch: {p.shape} vs {s.shape}")

    rows: list[dict[str, float]] = []
    for h in horizons:
        h = int(h)
        if h <= 0:
            raise ValueError(f"horizon must be positive; got {h}")
        fr = forward_returns(p, horizon=h, log=log_returns)
        res = ic(s, fr, method=method, horizon=h)
        rows.append(
            {
                "horizon": h,
                "ic": res.ic,
                "t_stat": res.t_stat,
                "p_value": res.p_value,
                "hit_rate": res.hit_rate,
                "n": res.n,
            }
        )
    return pd.DataFrame(rows).sort_values("horizon").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Alpha decay / signal half-life
# --------------------------------------------------------------------------- #
def autocorrelation(signal: npt.ArrayLike, *, max_lag: int = 20) -> FloatArray:
    """Sample autocorrelation function of a signal for lags ``0..max_lag``.

    Uses the standard biased estimator (divide by ``n``, not ``n - k``) so the
    ACF is positive semidefinite and lag 0 is exactly 1. nan entries are
    dropped before estimation.

    Returns
    -------
    numpy.ndarray
        Array of length ``max_lag + 1``; element ``k`` is the lag-``k``
        autocorrelation.
    """
    if max_lag < 1:
        raise ValueError("max_lag must be >= 1")
    x = np.asarray(signal, dtype=float).ravel()
    x = x[np.isfinite(x)]
    n = x.size
    if n < 2:
        raise ValueError("need at least two finite observations")
    xc = x - x.mean()
    var = float(xc @ xc)
    out = np.ones(max_lag + 1)
    if var == 0.0:
        out[1:] = np.nan
        return out
    for k in range(1, max_lag + 1):
        if k >= n:
            out[k] = np.nan
            continue
        out[k] = float(xc[: n - k] @ xc[k:]) / var
    return out


def signal_half_life(signal: npt.ArrayLike) -> float:
    """AR(1) mean-reversion half-life of a signal, in bars.

    Fits the AR(1) coefficient ``phi`` by regressing ``x_t`` on ``x_{t-1}``
    (the lag-1 autocorrelation for a demeaned series) and converts it to the
    half-life ``-ln(2) / ln(phi)`` — the number of bars for a shock to decay to
    half its size. A signal that persists (``phi`` near 1) has a long
    half-life; one that mean-reverts quickly has a short one.

    Returns
    -------
    float
        Half-life in bars. ``inf`` if the signal does not mean-revert
        (``phi >= 1``); ``nan`` if ``phi <= 0`` (no exponential
        interpretation) or the signal is degenerate.
    """
    x = np.asarray(signal, dtype=float).ravel()
    x = x[np.isfinite(x)]
    if x.size < 3:
        return float("nan")
    xc = x - x.mean()
    prev = xc[:-1]
    curr = xc[1:]
    denom = float(prev @ prev)
    if denom == 0.0:
        return float("nan")
    phi = float(prev @ curr) / denom
    if phi >= 1.0:
        return float("inf")
    if phi <= 0.0:
        return float("nan")
    return float(-np.log(2.0) / np.log(phi))


# --------------------------------------------------------------------------- #
# Capacity / market-impact
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CapacityResult:
    """Result of a square-root-impact capacity estimate.

    Attributes
    ----------
    capacity_notional:
        Maximum per-trade notional (same currency as ``adv_notional``) that
        keeps the Sharpe erosion at or below ``max_sharpe_erosion``.
    impact_at_capacity:
        Per-trade impact (as a return fraction) incurred at that notional.
    gross_edge:
        The gross per-trade edge implied by ``gross_sharpe`` and
        ``trades_per_year`` (return fraction per trade) — the budget that
        impact is allowed to erode.
    binding_erosion:
        The Sharpe-erosion fraction actually achieved at capacity (equals
        ``max_sharpe_erosion`` when the impact constraint binds).
    """

    capacity_notional: float
    impact_at_capacity: float
    gross_edge: float
    binding_erosion: float


def capacity_estimate(
    *,
    gross_sharpe: float,
    adv_notional: float,
    impact_coefficient: float,
    trades_per_year: float,
    max_sharpe_erosion: float = 0.10,
    volatility_per_trade: float = 0.01,
) -> CapacityResult:
    r"""Max deployable notional under a square-root market-impact model.

    The square-root law models the per-trade price impact of trading a
    participation rate ``q = notional / adv_notional`` as

    .. math::
        \text{impact} = \kappa \,\sigma\, \sqrt{q}
                      = \kappa \,\sigma\, \sqrt{\frac{N}{\text{ADV}}},

    where ``kappa`` is the (dimensionless) ``impact_coefficient`` and ``sigma``
    the per-trade volatility. Impact is a *cost* deducted from each trade's
    gross edge. The gross edge per trade is recovered from the strategy's
    annualised gross Sharpe as

    .. math::
        \text{edge} = \frac{\text{Sharpe}_\text{gross}\,\sigma}
                           {\sqrt{\text{trades\_per\_year}}},

    (Sharpe = mean / vol annualised ⇒ per-trade mean = Sharpe·σ/√T). We allow
    impact to erode at most ``max_sharpe_erosion`` of that edge; setting
    ``impact = erosion · edge`` and solving for ``N`` gives the closed-form
    capacity

    .. math::
        N^\* = \text{ADV}\,
               \left(\frac{\text{erosion}\cdot\text{edge}}{\kappa\,\sigma}\right)^2 .

    Because capacity scales as ``1 / kappa^2``, **raising the impact
    coefficient lowers capacity** — the core property the test pins down.

    Parameters
    ----------
    gross_sharpe:
        Annualised gross (pre-impact) Sharpe ratio of the strategy. Must be
        positive (a strategy with no edge has no capacity to speak of).
    adv_notional:
        Average daily volume in notional terms — the liquidity denominator in
        the participation rate. Must be positive.
    impact_coefficient:
        ``kappa`` in the square-root law. Larger = more impact per unit
        participation = less capacity. Must be positive.
    trades_per_year:
        Number of trades per year, used to convert annualised Sharpe to a
        per-trade edge. Must be positive.
    max_sharpe_erosion:
        Fraction of the per-trade edge you tolerate losing to impact (e.g.
        ``0.10`` = give up at most 10%). In ``(0, 1]``.
    volatility_per_trade:
        Per-trade return volatility ``sigma``. Must be positive.

    Returns
    -------
    CapacityResult
    """
    if gross_sharpe <= 0.0:
        raise ValueError("gross_sharpe must be positive (no edge ⇒ no capacity)")
    if adv_notional <= 0.0:
        raise ValueError("adv_notional must be positive")
    if impact_coefficient <= 0.0:
        raise ValueError("impact_coefficient must be positive")
    if trades_per_year <= 0.0:
        raise ValueError("trades_per_year must be positive")
    if not (0.0 < max_sharpe_erosion <= 1.0):
        raise ValueError("max_sharpe_erosion must be in (0, 1]")
    if volatility_per_trade <= 0.0:
        raise ValueError("volatility_per_trade must be positive")

    sigma = volatility_per_trade
    gross_edge = gross_sharpe * sigma / np.sqrt(trades_per_year)
    allowed_impact = max_sharpe_erosion * gross_edge
    # allowed_impact = kappa * sigma * sqrt(N / ADV)  =>  N = ADV * (allowed/(kappa*sigma))^2
    root = allowed_impact / (impact_coefficient * sigma)
    capacity = adv_notional * root * root

    return CapacityResult(
        capacity_notional=float(capacity),
        impact_at_capacity=float(allowed_impact),
        gross_edge=float(gross_edge),
        binding_erosion=float(max_sharpe_erosion),
    )
