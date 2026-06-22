"""Tests for the factor risk model + portfolio construction module.

These exercise the properties that make the module trustworthy as portfolio
plumbing: the shrinkage covariance is PSD and actually shrinks toward its
target; mean–variance respects the gross/dollar-neutral constraints; risk
parity equalises risk contributions; and the turnover penalty reduces churn.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from muninn.factor import (
    Constraints,
    FactorModel,
    PortfolioOptimizer,
    ledoit_wolf_shrinkage,
    risk_contributions,
)

UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE"]


def _sample_returns(seed: int = 0, n_obs: int = 250) -> np.ndarray:
    """Correlated 5-asset returns matrix (assets x time) with a market factor."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0, 0.02, size=n_obs)
    betas = np.array([1.0, 1.1, 1.4, 0.8, 1.6])
    idio = rng.normal(0.0, 0.01, size=(5, n_obs))
    return betas[:, None] * market[None, :] + idio


# --------------------------------------------------------------------------- #
# Shrinkage covariance
# --------------------------------------------------------------------------- #
def test_shrinkage_covariance_is_psd() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    eig = np.linalg.eigvalsh(cov)
    assert eig.min() >= -1e-12  # PSD up to float noise
    assert np.allclose(cov, cov.T)  # symmetric


def test_shrinkage_psd_when_assets_exceed_observations() -> None:
    # Sample covariance is singular here (n_obs < n_assets); shrinkage must
    # still produce an invertible, PSD estimate.
    returns = _sample_returns(n_obs=3)
    cov, delta = ledoit_wolf_shrinkage(returns)
    assert np.linalg.eigvalsh(cov).min() > 0.0
    assert delta > 0.0


def test_shrinkage_intensity_in_unit_interval() -> None:
    _, delta = ledoit_wolf_shrinkage(_sample_returns())
    assert 0.0 <= delta <= 1.0


def test_shrinkage_pulls_toward_target() -> None:
    # The shrunk estimate must sit strictly between the sample covariance and
    # the target, i.e. closer to the target than the raw sample is on the
    # off-diagonal correlations.
    returns = _sample_returns(seed=3, n_obs=40)
    x = returns - returns.mean(axis=1, keepdims=True)
    sample = (x @ x.T) / returns.shape[1]
    cov, delta = ledoit_wolf_shrinkage(returns, target="constant_correlation")
    assert delta > 0.0
    # Off-diagonal dispersion of correlations shrinks toward the common value.
    def offdiag_corr_spread(m: np.ndarray) -> float:
        d = np.sqrt(np.diag(m))
        corr = m / np.outer(d, d)
        n = m.shape[0]
        off = corr[~np.eye(n, dtype=bool)]
        return float(off.std())

    assert offdiag_corr_spread(cov) < offdiag_corr_spread(sample)


def test_identity_target_shrinks_eigenvalue_spread() -> None:
    returns = _sample_returns(seed=5, n_obs=30)
    x = returns - returns.mean(axis=1, keepdims=True)
    sample = (x @ x.T) / returns.shape[1]
    cov, _ = ledoit_wolf_shrinkage(returns, target="identity")
    cond_sample = np.linalg.cond(sample)
    cond_shrunk = np.linalg.cond(cov)
    assert cond_shrunk < cond_sample  # better conditioned


def test_shrinkage_intensity_matches_canonical_ledoit_wolf() -> None:
    # Pins delta on a fixed 4-asset x 6-obs input against the canonical
    # Ledoit & Wolf (2003 JEF / 2004) constant-correlation `covCor` estimator,
    # cross-checked with an independent reference computation. This guards the
    # off-diagonal rho term: a diagonal-only rho approximation gives a visibly
    # different (inflated) intensity here.
    x = np.array(
        [
            [0.10, -0.05, 0.02, 0.08, -0.03, 0.04],
            [0.07, -0.02, 0.01, 0.06, -0.04, 0.03],
            [-0.06, 0.04, -0.01, -0.05, 0.02, -0.02],
            [0.09, -0.03, 0.03, 0.07, -0.05, 0.05],
        ]
    )
    _, delta_cc = ledoit_wolf_shrinkage(x, target="constant_correlation")
    _, delta_id = ledoit_wolf_shrinkage(x, target="identity")
    assert delta_cc == pytest.approx(0.09259794749880958, rel=0, abs=1e-12)
    assert delta_id == pytest.approx(0.08893563016722039, rel=0, abs=1e-12)
    assert 0.0 <= delta_cc <= 1.0
    assert 0.0 <= delta_id <= 1.0


def test_shrinkage_intensity_decreases_with_more_observations() -> None:
    # Core Ledoit-Wolf property: with more observations the sample covariance is
    # better estimated, so the optimal shrinkage toward the structured target
    # falls. A buy-side reviewer flagged the intensity as non-monotone in T and
    # saturating at 1.0; the canonical estimator is asymptotically monotone.
    # We use nested prefixes of ONE long series so only T changes (not the
    # realised noise), and check the decline over the asymptotic regime.
    rng = np.random.default_rng(42)
    market = rng.normal(0.0, 0.02, size=2000)
    betas = np.array([1.0, 1.1, 1.4, 0.8, 1.6])
    idio = rng.normal(0.0, 0.01, size=(5, 2000))
    full = betas[:, None] * market[None, :] + idio

    deltas = {}
    for t in (320, 640, 1280, 2000):
        _, d = ledoit_wolf_shrinkage(full[:, :t], target="constant_correlation")
        assert 0.0 <= d <= 1.0
        deltas[t] = d
    # Strictly decreasing across the asymptotic regime, and well off the 1.0
    # saturation the buggy diagonal-only rho produced.
    assert deltas[320] > deltas[640] > deltas[1280] > deltas[2000]
    assert deltas[320] < 1.0
    # Pin the canonical values so a regression in the rho formula is caught.
    assert deltas[320] == pytest.approx(0.616445, abs=1e-5)
    assert deltas[2000] == pytest.approx(0.068932, abs=1e-5)


def test_identity_target_rho_is_diagonal_exact() -> None:
    # For the identity target the off-diagonal of F is the constant 0 and does
    # not depend on the data, so the canonical rho equals its diagonal part.
    # Recomputing delta with an explicit diagonal-only rho must agree.
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 0.01, size=(5, 200))
    _, delta = ledoit_wolf_shrinkage(returns, target="identity")

    xc = returns - returns.mean(axis=1, keepdims=True)
    n_obs = returns.shape[1]
    sample = (xc @ xc.T) / n_obs
    mu = np.trace(sample) / sample.shape[0]
    f = mu * np.eye(sample.shape[0])
    y = xc.T
    y2 = y * y
    pi_mat = (y2.T @ y2) / n_obs - sample * sample
    pi = pi_mat.sum()
    gamma = np.sum((f - sample) ** 2)
    rho = np.trace(pi_mat)  # diagonal-only is exact for identity
    expected = min(1.0, max(0.0, (pi - rho) / gamma / n_obs))
    assert delta == pytest.approx(expected, abs=1e-12)


def test_shrinkage_rejects_single_observation() -> None:
    with pytest.raises(ValueError):
        ledoit_wolf_shrinkage(np.zeros((5, 1)))


def test_shrinkage_rejects_unknown_target() -> None:
    with pytest.raises(ValueError):
        ledoit_wolf_shrinkage(_sample_returns(), target="nonsense")


# --------------------------------------------------------------------------- #
# Factor model
# --------------------------------------------------------------------------- #
def test_factor_model_fit_produces_expected_shapes() -> None:
    df = pd.DataFrame(_sample_returns(), index=UNIVERSE)
    fm = FactorModel().fit(df)
    assert fm.exposures.shape == (5, 3)
    assert list(fm.exposures.columns) == ["market_beta", "momentum", "volatility"]
    assert list(fm.exposures.index) == UNIVERSE
    assert fm.covariance.shape == (5, 5)


def test_factor_model_market_beta_recovers_construction() -> None:
    # Returns are built as beta_i * market + idio; fitted market_beta should
    # recover the construction betas in rank order.
    fm = FactorModel().fit(_sample_returns(seed=7))
    fitted = fm.exposures["market_beta"].to_numpy()
    true_betas = np.array([1.0, 1.1, 1.4, 0.8, 1.6])
    assert np.argmax(fitted) == np.argmax(true_betas)  # DOGE highest beta
    assert np.argmin(fitted) == np.argmin(true_betas)  # XRP lowest beta


def test_factor_model_requires_fit_before_access() -> None:
    with pytest.raises(RuntimeError):
        _ = FactorModel().covariance


def test_factor_model_rejects_too_few_observations() -> None:
    with pytest.raises(ValueError):
        FactorModel().fit(np.zeros((5, 1)))


# --------------------------------------------------------------------------- #
# Mean-variance optimiser
# --------------------------------------------------------------------------- #
def test_mean_variance_respects_dollar_neutral_and_gross_cap() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas, cov, constraints=Constraints(dollar_neutral=True, gross_leverage=1.0)
    )
    assert abs(w.sum()) < 1e-10  # dollar-neutral
    assert abs(np.abs(w).sum() - 1.0) < 1e-10  # gross leverage == 1


def test_mean_variance_gross_leverage_scales() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas, cov, constraints=Constraints(dollar_neutral=True, gross_leverage=2.0)
    )
    assert abs(np.abs(w).sum() - 2.0) < 1e-10


def test_mean_variance_long_only_has_no_shorts() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas, cov, constraints=Constraints(long_only=True, gross_leverage=1.0)
    )
    assert (w >= -1e-12).all()
    assert abs(w.sum() - 1.0) < 1e-10  # long-only + gross 1 => fully invested


def test_mean_variance_tilts_toward_higher_alpha() -> None:
    # With an (almost) diagonal cov, the highest-alpha asset gets the largest
    # long and the lowest-alpha the largest short.
    cov = np.diag([1.0, 1.0, 1.0, 1.0, 1.0]) * 1e-4
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas, cov, constraints=Constraints(dollar_neutral=True, gross_leverage=1.0)
    )
    assert np.argmax(w) == np.argmax(alphas)
    assert np.argmin(w) == np.argmin(alphas)


def test_turnover_penalty_reduces_churn() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w0 = np.array([0.5, -0.5, 0.0, 0.0, 0.0])
    opt = PortfolioOptimizer()
    constraints = Constraints(dollar_neutral=True, gross_leverage=1.0)
    w_unpenalised = opt.mean_variance(
        alphas, cov, constraints=constraints, current_weights=w0, cost_penalty=0.0
    )
    w_penalised = opt.mean_variance(
        alphas, cov, constraints=constraints, current_weights=w0, cost_penalty=100.0
    )
    churn_un = np.abs(w_unpenalised - w0).sum()
    churn_pen = np.abs(w_penalised - w0).sum()
    assert churn_pen < churn_un


def test_mean_variance_solvable_with_singular_cov() -> None:
    # A rank-deficient covariance must not blow up the solve (ridge guards it).
    cov = np.outer(np.ones(5), np.ones(5)) * 1e-4  # rank 1
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas, cov, constraints=Constraints(dollar_neutral=True, gross_leverage=1.0)
    )
    assert np.isfinite(w).all()


# --------------------------------------------------------------------------- #
# Factor neutralisation
# --------------------------------------------------------------------------- #
def test_neutralize_orthogonalises_against_exposures() -> None:
    fm = FactorModel().fit(_sample_returns(seed=11))
    b = fm.exposures.to_numpy()
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    resid = PortfolioOptimizer.neutralize(alphas, b)
    # Residual is orthogonal to each factor column (and the intercept).
    design = np.column_stack([np.ones(5), b])
    assert np.allclose(design.T @ resid, 0.0, atol=1e-9)


def test_factor_neutral_mean_variance_runs() -> None:
    fm = FactorModel().fit(_sample_returns(seed=12))
    cov = fm.covariance
    b = fm.exposures.to_numpy()
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])
    w = PortfolioOptimizer().mean_variance(
        alphas,
        cov,
        constraints=Constraints(dollar_neutral=True, gross_leverage=1.0, factor_neutral=True),
        exposures=b,
    )
    assert abs(w.sum()) < 1e-10
    assert np.isfinite(w).all()


def test_factor_neutral_requires_exposures() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    with pytest.raises(ValueError):
        PortfolioOptimizer().mean_variance(
            np.zeros(5), cov, constraints=Constraints(factor_neutral=True)
        )


# --------------------------------------------------------------------------- #
# Risk parity
# --------------------------------------------------------------------------- #
def test_risk_parity_equalises_contributions_on_diagonal_cov() -> None:
    cov = np.diag([1.0, 4.0, 9.0, 16.0, 25.0])
    w = PortfolioOptimizer().risk_parity(cov, constraints=Constraints(gross_leverage=1.0))
    rc = risk_contributions(w, cov)
    assert np.allclose(rc, 0.2, atol=1e-6)  # equal risk contribution


def test_risk_parity_matches_inverse_vol_on_diagonal() -> None:
    cov = np.diag([1.0, 4.0, 9.0, 16.0, 25.0])
    w = PortfolioOptimizer().risk_parity(cov, constraints=Constraints(gross_leverage=1.0))
    sigma = np.sqrt(np.diag(cov))
    expected = (1.0 / sigma) / (1.0 / sigma).sum()
    assert np.allclose(w, expected, atol=1e-6)


def test_risk_parity_equalises_contributions_on_correlated_cov() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns(seed=21))
    w = PortfolioOptimizer().risk_parity(cov, constraints=Constraints(gross_leverage=1.0))
    rc = risk_contributions(w, cov)
    assert np.allclose(rc, rc.mean(), atol=1e-4)


def test_risk_parity_rejects_dollar_neutral() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    with pytest.raises(ValueError):
        PortfolioOptimizer().risk_parity(cov, constraints=Constraints(dollar_neutral=True))


# --------------------------------------------------------------------------- #
# Constraint guards
# --------------------------------------------------------------------------- #
def test_dollar_neutral_and_long_only_are_mutually_exclusive() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    with pytest.raises(ValueError):
        PortfolioOptimizer().mean_variance(
            np.zeros(5),
            cov,
            constraints=Constraints(dollar_neutral=True, long_only=True),
        )


def test_optimizer_rejects_non_positive_risk_aversion() -> None:
    with pytest.raises(ValueError):
        PortfolioOptimizer(risk_aversion=0.0)


def test_risk_contributions_sum_to_one() -> None:
    cov, _ = ledoit_wolf_shrinkage(_sample_returns())
    w = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
    rc = risk_contributions(w, cov)
    assert abs(rc.sum() - 1.0) < 1e-12
