"""Tests for the alpha-research diagnostics module (IC, decay, capacity).

These pin the properties that make the diagnostics trustworthy:

- IC has the *correct sign* on a constructed alpha with known predictivity,
  and its t-stat / p-value flag a genuine signal as significant and noise as
  not.
- The IC decay curve fades monotonically for a microstructure-style alpha
  whose edge lives at the shortest horizon.
- Signal half-life is finite and ordered for AR(1) processes (more persistent
  ⇒ longer half-life), and the autocorrelation decays monotonically for AR(1).
- Capacity decreases as the market-impact coefficient rises (the headline
  capacity property) and scales as 1/kappa^2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from muninn.research import (
    CapacityResult,
    ICResult,
    autocorrelation,
    capacity_estimate,
    forward_returns,
    ic,
    ic_decay_curve,
    rank_ic,
    signal_half_life,
)


# --------------------------------------------------------------------------- #
# Forward returns
# --------------------------------------------------------------------------- #
def test_forward_returns_simple_and_tail_nan():
    prices = np.array([100.0, 110.0, 121.0, 133.1])
    fr = forward_returns(prices, horizon=1)
    assert fr[0] == pytest.approx(0.10)
    assert fr[1] == pytest.approx(0.10)
    assert fr[2] == pytest.approx(0.10)
    assert np.isnan(fr[3])  # last bar has no forward observation


def test_forward_returns_log_matches_log_ratio():
    prices = np.array([100.0, 105.0, 110.25])
    fr = forward_returns(prices, horizon=1, log=True)
    assert fr[0] == pytest.approx(np.log(1.05))


def test_forward_returns_rejects_nonpositive_horizon():
    with pytest.raises(ValueError):
        forward_returns([1.0, 2.0], horizon=0)


# --------------------------------------------------------------------------- #
# Information coefficient — sign correctness on known predictivity
# --------------------------------------------------------------------------- #
def _predictive_panel(seed: int = 0, n: int = 500, beta: float = 1.0):
    """Construct a signal that predicts the *next-bar* return with known sign.

    forward_return[t] = beta * signal[t] + noise, so a positive beta must yield
    a positive IC and a negative beta a negative IC.
    """
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    noise = rng.normal(scale=0.5, size=n)
    fwd = beta * signal + noise
    return signal, fwd


def test_ic_sign_positive_for_positive_predictivity():
    signal, fwd = _predictive_panel(beta=1.0)
    res = ic(signal, fwd, method="spearman")
    assert isinstance(res, ICResult)
    assert res.ic > 0.3
    assert res.p_value < 1e-6  # genuinely significant


def test_ic_sign_negative_for_negative_predictivity():
    signal, fwd = _predictive_panel(beta=-1.0)
    res = ic(signal, fwd, method="spearman")
    assert res.ic < -0.3
    assert res.p_value < 1e-6


def test_pearson_and_spearman_agree_in_sign():
    signal, fwd = _predictive_panel(beta=0.8)
    sp = ic(signal, fwd, method="spearman").ic
    pe = ic(signal, fwd, method="pearson").ic
    assert np.sign(sp) == np.sign(pe) == 1.0


def test_ic_of_pure_noise_is_insignificant():
    rng = np.random.default_rng(7)
    signal = rng.normal(size=500)
    fwd = rng.normal(size=500)  # independent of signal
    res = ic(signal, fwd, method="spearman")
    assert abs(res.ic) < 0.15
    assert res.p_value > 0.01  # cannot reject the null of no predictivity


def test_ic_t_stat_matches_closed_form():
    signal, fwd = _predictive_panel(beta=0.6, n=300)
    res = ic(signal, fwd, method="pearson")
    n = res.n
    expected_t = res.ic * np.sqrt((n - 2) / (1 - res.ic**2))
    assert res.t_stat == pytest.approx(expected_t, rel=1e-9)


def test_ic_hit_rate_in_unit_interval():
    signal, fwd = _predictive_panel(beta=1.0)
    res = ic(signal, fwd)
    assert 0.5 < res.hit_rate <= 1.0  # predictive signal beats a coin flip


def test_ic_handles_nans_pairwise():
    signal = np.array([1.0, 2.0, np.nan, 4.0, 5.0, 6.0])
    fwd = np.array([1.0, np.nan, 3.0, 4.0, 5.0, 6.0])
    res = ic(signal, fwd, method="pearson")
    assert res.n == 4  # only the four fully-finite pairs survive
    assert res.ic == pytest.approx(1.0)


def test_ic_too_few_points_returns_nan():
    res = ic([1.0, 2.0], [1.0, 2.0])
    assert np.isnan(res.ic)
    assert res.n == 2


def test_ic_rejects_bad_method():
    with pytest.raises(ValueError):
        ic([1, 2, 3], [1, 2, 3], method="kendall")


def test_rank_ic_convenience_matches_full():
    signal, fwd = _predictive_panel(beta=0.7)
    assert rank_ic(signal, fwd) == pytest.approx(
        ic(signal, fwd, method="spearman").ic
    )


# --------------------------------------------------------------------------- #
# IC decay curve — monotone fade for a short-horizon alpha
# --------------------------------------------------------------------------- #
def test_ic_decay_curve_columns_and_horizons():
    rng = np.random.default_rng(1)
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, size=400))
    signal = rng.normal(size=400)
    df = ic_decay_curve(prices, signal, horizons=(1, 5, 15))
    assert list(df["horizon"]) == [1, 5, 15]
    assert {"horizon", "ic", "t_stat", "p_value", "hit_rate", "n"}.issubset(
        df.columns
    )
    assert isinstance(df, pd.DataFrame)


def test_ic_decay_curve_fades_for_single_bar_alpha():
    """A signal that only predicts the *next* bar's return must show IC that
    decays as the horizon grows: |IC(1)| > |IC(5)| > |IC(15)|."""
    rng = np.random.default_rng(3)
    n = 4000
    # Per-bar returns are driven by the contemporaneous signal only.
    signal = rng.normal(size=n)
    per_bar = 0.6 * signal + rng.normal(scale=0.5, size=n)
    # Build prices so that return from t to t+1 equals per_bar[t].
    # forward_returns(prices, 1)[t] = prices[t+1]/prices[t]-1 = per_bar[t].
    prices = np.empty(n + 1)
    prices[0] = 100.0
    prices[1:] = 100.0 * np.cumprod(1.0 + per_bar)
    sig_aligned = np.concatenate([signal, [np.nan]])  # align to prices length
    df = ic_decay_curve(prices, sig_aligned, horizons=(1, 5, 15))
    ics = df.set_index("horizon")["ic"].abs()
    assert ics[1] > ics[5] > ics[15]
    # And the curve is strictly decreasing across all evaluated horizons.
    assert (np.diff(ics.to_numpy()) < 0).all()


def test_ic_decay_curve_length_mismatch_raises():
    with pytest.raises(ValueError):
        ic_decay_curve([1.0, 2.0, 3.0], [1.0, 2.0], horizons=(1,))


# --------------------------------------------------------------------------- #
# Autocorrelation + half-life
# --------------------------------------------------------------------------- #
def _ar1(phi: float, n: int = 20000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    eps = rng.normal(size=n)
    x = np.empty(n)
    x[0] = eps[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + eps[t]
    return x


def test_autocorrelation_lag0_is_one():
    acf = autocorrelation(_ar1(0.5), max_lag=10)
    assert acf[0] == pytest.approx(1.0)
    assert acf.shape == (11,)


def test_autocorrelation_monotone_decay_for_ar1():
    """For an AR(1) the population ACF is phi^k — strictly decreasing in k for
    0 < phi < 1. The sample ACF over a long path should reflect that."""
    acf = autocorrelation(_ar1(0.7, n=50000), max_lag=8)
    diffs = np.diff(acf)
    assert (diffs < 0).all()  # strictly decreasing
    # Lag-1 autocorrelation should be close to phi.
    assert acf[1] == pytest.approx(0.7, abs=0.03)


def test_autocorrelation_requires_min_lag():
    with pytest.raises(ValueError):
        autocorrelation([1.0, 2.0, 3.0], max_lag=0)


def test_half_life_increases_with_persistence():
    """More persistent AR(1) (phi closer to 1) ⇒ longer half-life."""
    hl_fast = signal_half_life(_ar1(0.3, n=40000))
    hl_slow = signal_half_life(_ar1(0.9, n=40000))
    assert hl_fast < hl_slow
    # Analytic check: half-life ≈ -ln2 / ln(phi).
    assert hl_slow == pytest.approx(-np.log(2) / np.log(0.9), rel=0.15)


def test_half_life_infinite_for_random_walk():
    rng = np.random.default_rng(0)
    walk = np.cumsum(rng.normal(size=5000))  # phi ~ 1
    hl = signal_half_life(walk)
    assert hl == float("inf") or hl > 200  # effectively non-reverting


def test_half_life_nan_for_degenerate():
    assert np.isnan(signal_half_life([1.0, 1.0, 1.0, 1.0]))


# --------------------------------------------------------------------------- #
# Capacity / market impact
# --------------------------------------------------------------------------- #
def _base_capacity(**overrides) -> CapacityResult:
    kw = {
        "gross_sharpe": 2.0,
        "adv_notional": 1_000_000.0,
        "impact_coefficient": 0.5,
        "trades_per_year": 2520.0,
        "max_sharpe_erosion": 0.10,
        "volatility_per_trade": 0.01,
    }
    kw.update(overrides)
    return capacity_estimate(**kw)


def test_capacity_decreases_as_impact_coefficient_rises():
    low = _base_capacity(impact_coefficient=0.2).capacity_notional
    mid = _base_capacity(impact_coefficient=0.5).capacity_notional
    high = _base_capacity(impact_coefficient=1.0).capacity_notional
    assert low > mid > high


def test_capacity_scales_inverse_square_in_impact():
    """N* ∝ 1/kappa^2: doubling kappa quarters the capacity."""
    base = _base_capacity(impact_coefficient=0.5).capacity_notional
    doubled = _base_capacity(impact_coefficient=1.0).capacity_notional
    assert doubled == pytest.approx(base / 4.0, rel=1e-9)


def test_capacity_increases_with_liquidity_and_erosion_budget():
    more_liquid = _base_capacity(adv_notional=2_000_000.0).capacity_notional
    base = _base_capacity().capacity_notional
    assert more_liquid > base
    more_tolerant = _base_capacity(max_sharpe_erosion=0.20).capacity_notional
    assert more_tolerant > base


def test_capacity_increases_with_gross_sharpe():
    strong = _base_capacity(gross_sharpe=3.0).capacity_notional
    weak = _base_capacity(gross_sharpe=1.0).capacity_notional
    assert strong > weak


def test_capacity_impact_at_capacity_equals_eroded_edge():
    res = _base_capacity()
    assert res.impact_at_capacity == pytest.approx(
        res.binding_erosion * res.gross_edge, rel=1e-12
    )


def test_capacity_rejects_invalid_inputs():
    for bad in [
        {"gross_sharpe": 0.0},
        {"adv_notional": -1.0},
        {"impact_coefficient": 0.0},
        {"trades_per_year": 0.0},
        {"max_sharpe_erosion": 0.0},
        {"max_sharpe_erosion": 1.5},
        {"volatility_per_trade": 0.0},
    ]:
        with pytest.raises(ValueError):
            _base_capacity(**bad)
