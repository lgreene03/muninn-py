"""Factor risk model + portfolio construction — the discipline amateurs skip.

This module is the *portfolio-construction* half of the Muninn research SDK.
The notebook helpers (:mod:`muninn.notebook`) get you from raw features to an
alpha score per asset; this module gets you from a panel of alpha scores plus a
returns history to a set of **target weights** you could actually trade, with a
covariance estimate that is well-conditioned enough to optimise against.

It is deliberately small, dependency-light (numpy + pandas only — no scipy,
no sklearn) and self-contained: nothing here imports the HTTP client, so it is
usable on any returns matrix, not just one pulled from Muninn.

Two pieces fit together:

``FactorModel``
    Cross-sectional factor model over a universe (e.g. BTC/ETH/SOL/XRP/DOGE).
    From a returns matrix it builds interpretable factor exposures — market /
    equal-weight beta, cross-sectional momentum, and volatility — and a rolling
    covariance matrix with **Ledoit–Wolf-style shrinkage** toward a structured
    target so the matrix is well-conditioned (PSD, invertible) even when the
    number of assets approaches the number of observations.

``PortfolioOptimizer``
    Turns alpha scores + a covariance estimate into target weights. Supports
    mean–variance with a turnover / transaction-cost penalty (closed-form
    ridge-regularised solve) and iterative risk-parity, under dollar-neutral
    and gross-leverage constraints, with optional factor-neutralisation of the
    alphas.

Conventions
-----------
- **Returns matrix is (assets x time).** Rows are assets, columns are
  observations ordered ascending in time. This matches "one row per
  instrument" research panels and keeps the cross-sectional maths readable.
- **numpy in, numpy out** for the optimiser core; the high-level helpers
  accept and return :class:`pandas.DataFrame` / :class:`pandas.Series` so
  asset labels survive.
- **No wall-clock reads, no mutation.** Every method returns new arrays.

Example
-------
.. code-block:: python

    import numpy as np
    from muninn.factor import FactorModel, PortfolioOptimizer, Constraints

    # returns: 5 assets x 250 days
    fm = FactorModel().fit(returns)
    cov = fm.covariance                      # shrunk, PSD
    alphas = np.array([0.3, -0.1, 0.2, 0.0, -0.4])

    opt = PortfolioOptimizer()
    w = opt.mean_variance(
        alphas, cov,
        constraints=Constraints(dollar_neutral=True, gross_leverage=1.0),
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import numpy.typing as npt
import pandas as pd

__all__ = [
    "Constraints",
    "FactorModel",
    "PortfolioOptimizer",
    "ledoit_wolf_shrinkage",
    "risk_contributions",
]

FloatArray = npt.NDArray[np.float64]


def _zscore(v: FloatArray) -> FloatArray:
    """Cross-sectional z-score, robust to a zero-variance vector (returns 0s)."""
    v = np.asarray(v, dtype=float)
    sd = v.std()
    if sd == 0.0:
        return np.zeros_like(v)
    return (v - v.mean()) / sd


# --------------------------------------------------------------------------- #
# Covariance estimation
# --------------------------------------------------------------------------- #
def ledoit_wolf_shrinkage(
    returns: FloatArray,
    *,
    target: str = "constant_correlation",
) -> tuple[FloatArray, float]:
    """Ledoit–Wolf-style shrinkage covariance estimator (pure numpy).

    Shrinks the noisy sample covariance ``S`` toward a structured ``target``
    ``F``::

        Sigma_hat = delta * F + (1 - delta) * S

    The shrinkage intensity ``delta in [0, 1]`` is estimated from the data with
    the Ledoit–Wolf oracle: it is large when ``S`` is noisy (few observations
    relative to assets) and small when ``S`` is already well-estimated. The
    result is always positive semidefinite as long as the target is PSD, which
    both supported targets are.

    Parameters
    ----------
    returns:
        ``(n_assets, n_obs)`` matrix of (excess) returns. Rows are assets.
    target:
        Shrinkage target. ``"constant_correlation"`` (default) keeps each
        asset's sample variance but replaces every pairwise correlation with
        the average correlation — the Ledoit–Wolf (2004) recommendation, good
        when assets are positively co-moving (as crypto majors are).
        ``"identity"`` shrinks toward ``mu * I`` (scaled identity), the
        diagonal target from Ledoit–Wolf (2004) "Honey, I shrunk the sample
        covariance matrix".

    Returns
    -------
    (cov, delta):
        The shrunk covariance ``(n_assets, n_assets)`` and the estimated
        shrinkage intensity ``delta``.

    Raises
    ------
    ValueError
        If fewer than two observations are supplied or ``target`` is unknown.
    """
    x = np.asarray(returns, dtype=float)
    if x.ndim != 2:
        raise ValueError("returns must be a 2-D (assets x time) array")
    n_assets, n_obs = x.shape
    if n_obs < 2:
        raise ValueError("need at least two observations to estimate covariance")

    # Demean across time (each asset's mean return removed).
    mean = x.mean(axis=1, keepdims=True)
    xc = x - mean
    # Sample covariance with the 1/n (MLE) normalisation used by Ledoit–Wolf.
    sample = (xc @ xc.T) / n_obs

    if target == "identity":
        mu = np.trace(sample) / n_assets
        f = mu * np.eye(n_assets)
    elif target == "constant_correlation":
        f = _constant_correlation_target(sample)
    else:  # pragma: no cover - guarded by the public API
        raise ValueError(f"unknown shrinkage target: {target!r}")

    delta = _shrinkage_intensity(xc, sample, f, n_obs, target=target)
    cov = delta * f + (1.0 - delta) * sample
    # Symmetrise to kill floating-point asymmetry before downstream solves.
    cov = 0.5 * (cov + cov.T)
    return cov, float(delta)


def _constant_correlation_target(sample: FloatArray) -> FloatArray:
    """Constant-correlation target: sample variances, averaged off-diagonal corr."""
    var = np.diag(sample)
    std = np.sqrt(np.clip(var, 0.0, None))
    denom = np.outer(std, std)
    # Avoid divide-by-zero for a degenerate (zero-variance) asset.
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, sample / denom, 0.0)
    n = sample.shape[0]
    off = corr[~np.eye(n, dtype=bool)]
    rbar = off.mean() if off.size else 0.0
    target_corr = np.full((n, n), rbar)
    np.fill_diagonal(target_corr, 1.0)
    return cast(FloatArray, target_corr * denom)


def _shrinkage_intensity(
    xc: FloatArray,
    sample: FloatArray,
    f: FloatArray,
    n_obs: int,
    *,
    target: str,
) -> float:
    """Estimate the optimal shrinkage intensity ``delta`` (clipped to [0, 1]).

    Implements the Ledoit–Wolf decomposition ``delta = (pi - rho) / gamma / n``
    of *Honey, I Shrunk the Sample Covariance Matrix* (Ledoit & Wolf, 2004) and
    *Improved Estimation of the Covariance Matrix of Stock Returns* (Ledoit &
    Wolf, 2003, JEF) — the canonical ``covCor`` / ``cov1Para`` estimators — where

    - ``pi`` is the sum over ``i,j`` of the asymptotic variances of the
      sample-covariance entries ``s_ij``;
    - ``gamma`` is the squared Frobenius distance between the sample covariance
      and the shrinkage target ``F``;
    - ``rho`` is the sum of asymptotic covariances between the sample-covariance
      entries and the (data-dependent) target entries.

    The subtle term is ``rho``. For the **identity** target the off-diagonal of
    ``F`` is the constant ``0`` and does not depend on the data, so ``rho``
    collapses to its diagonal part ``sum_i Var(s_ii)`` exactly. For the
    **constant-correlation** target the off-diagonal target entries
    ``rbar * sqrt(s_ii s_jj)`` *do* depend on the sample variances, contributing
    a non-zero off-diagonal term to ``rho``. Dropping that term (a "diagonal
    approximation") systematically *understates* ``rho``, inflating
    ``pi - rho`` and ``kappa``, which drives the intensity to saturate at
    ``1.0`` and makes it non-monotone in the sample size ``T`` — the failure a
    buy-side review flagged. We therefore compute the full canonical ``rho``,
    matching Ledoit & Wolf's reference ``covCor`` implementation exactly.
    """
    n_assets = sample.shape[0]
    # Work in (obs x assets) layout to mirror the canonical reference code.
    y = xc.T  # (n_obs, n_assets)

    # pi: sum over i,j of Var(s_ij). pi_mat[i,j] = mean_t (y_ti y_tj)^2 - s_ij^2.
    y2 = y * y
    pi_mat = (y2.T @ y2) / n_obs - sample * sample
    pi = float(pi_mat.sum())

    # gamma: squared Frobenius distance between target and sample.
    gamma = float(np.sum((f - sample) ** 2))
    if gamma <= 0.0:
        return 0.0

    # rho: diagonal part is exact; the off-diagonal part is non-zero only for a
    # data-dependent target (constant-correlation), and zero for the identity
    # target (whose off-diagonal entries are the constant 0).
    rho_diag = float(np.trace(pi_mat))
    rho_off = 0.0
    if target == "constant_correlation":
        samplevar = np.diag(sample)
        sqrtvar = np.sqrt(np.clip(samplevar, 0.0, None))
        denom = np.outer(sqrtvar, sqrtvar)
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(denom > 0, sample / denom, 0.0)
        off_mask = ~np.eye(n_assets, dtype=bool)
        off = corr[off_mask]
        rbar = float(off.mean()) if off.size else 0.0
        # thetaMat[i,j] = AsyCov(s_ij, s_ii) estimate = E[y_ti^2 y_ti y_tj] - s_ii s_ij
        term1 = ((y**3).T @ y) / n_obs
        term2 = samplevar[:, None] * sample
        theta_mat = term1 - term2
        np.fill_diagonal(theta_mat, 0.0)
        # scale[i,j] = sqrt(s_jj / s_ii); guarded against a zero-variance asset.
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(
                sqrtvar[:, None] > 0, sqrtvar[None, :] / sqrtvar[:, None], 0.0
            )
        rho_off = rbar * float(np.sum(scale * theta_mat))

    rho = rho_diag + rho_off
    kappa = (pi - rho) / gamma
    delta = kappa / n_obs
    return float(min(1.0, max(0.0, delta)))


# --------------------------------------------------------------------------- #
# Factor model
# --------------------------------------------------------------------------- #
class FactorModel:
    """Cross-sectional factor model over a fixed asset universe.

    Given a returns matrix it computes, per asset:

    - **market beta** — slope of the asset's returns on the equal-weight
      universe return (the crypto "market factor");
    - **momentum** — trailing cumulative return over a lookback window,
      cross-sectionally z-scored;
    - **volatility** — sample standard deviation of returns, cross-sectionally
      z-scored.

    and a shrunk covariance matrix (:func:`ledoit_wolf_shrinkage`). The factor
    exposures form a matrix ``B`` (assets x factors) usable for
    factor-neutralising alphas in :class:`PortfolioOptimizer`.

    The model is intentionally descriptive, not predictive: it characterises
    the *risk* structure of the universe so the optimiser has well-behaved
    inputs. That separation — alpha elsewhere, risk here — is the point.
    """

    def __init__(
        self,
        *,
        momentum_lookback: int | None = None,
        shrinkage_target: str = "constant_correlation",
    ) -> None:
        """Initialise the model.

        Parameters
        ----------
        momentum_lookback:
            Number of trailing observations for the momentum factor. ``None``
            (default) uses the full sample.
        shrinkage_target:
            Target passed through to :func:`ledoit_wolf_shrinkage`.
        """
        self.momentum_lookback = momentum_lookback
        self.shrinkage_target = shrinkage_target
        self._fitted = False
        self.assets: list[str] | None = None
        self.factor_names: list[str] = ["market_beta", "momentum", "volatility"]
        self.exposures_: FloatArray | None = None
        self.covariance_: FloatArray | None = None
        self.shrinkage_: float | None = None

    # -- fitting ----------------------------------------------------------- #
    def fit(self, returns: pd.DataFrame | FloatArray) -> FactorModel:
        """Fit the factor model to a returns matrix.

        Parameters
        ----------
        returns:
            ``(n_assets, n_obs)`` matrix. A :class:`pandas.DataFrame` is
            accepted (its index is kept as the asset labels); a plain numpy
            array gets integer labels.

        Returns
        -------
        self
            The fitted model (chainable).
        """
        if isinstance(returns, pd.DataFrame):
            self.assets = [str(a) for a in returns.index]
            x = returns.to_numpy(dtype=float)
        else:
            x = np.asarray(returns, dtype=float)
            self.assets = [str(i) for i in range(x.shape[0])]

        if x.ndim != 2 or x.shape[1] < 2:
            raise ValueError("returns must be (assets x time) with >= 2 observations")

        market = x.mean(axis=0)  # equal-weight universe return per period
        beta = self._market_beta(x, market)
        momentum = self._momentum(x)
        vol = x.std(axis=1)

        exposures = np.column_stack(
            [beta, _zscore(momentum), _zscore(vol)]
        )
        self.exposures_ = exposures

        cov, delta = ledoit_wolf_shrinkage(x, target=self.shrinkage_target)
        self.covariance_ = cov
        self.shrinkage_ = delta
        self._fitted = True
        return self

    @staticmethod
    def _market_beta(x: FloatArray, market: FloatArray) -> FloatArray:
        mc = market - market.mean()
        denom = float(mc @ mc)
        if denom <= 0.0:
            return np.zeros(x.shape[0])
        xc = x - x.mean(axis=1, keepdims=True)
        return (xc @ mc) / denom

    def _momentum(self, x: FloatArray) -> FloatArray:
        window = x if self.momentum_lookback is None else x[:, -self.momentum_lookback :]
        # Cumulative log-style return over the window: sum of period returns.
        return window.sum(axis=1)

    # -- accessors --------------------------------------------------------- #
    def _check(self) -> None:
        if not self._fitted:
            raise RuntimeError("FactorModel.fit() must be called before accessing results")

    @property
    def covariance(self) -> FloatArray:
        """Shrunk covariance matrix ``(n_assets, n_assets)``."""
        self._check()
        assert self.covariance_ is not None
        return self.covariance_

    @property
    def shrinkage_intensity(self) -> float:
        """Estimated Ledoit–Wolf shrinkage intensity ``delta in [0, 1]``."""
        self._check()
        assert self.shrinkage_ is not None
        return self.shrinkage_

    @property
    def exposures(self) -> pd.DataFrame:
        """Factor-exposure matrix ``B`` as a DataFrame (assets x factors)."""
        self._check()
        assert self.exposures_ is not None and self.assets is not None
        return pd.DataFrame(self.exposures_, index=self.assets, columns=self.factor_names)


# --------------------------------------------------------------------------- #
# Portfolio construction
# --------------------------------------------------------------------------- #
@dataclass
class Constraints:
    """Portfolio constraints shared by the optimiser routines.

    Parameters
    ----------
    dollar_neutral:
        If ``True``, weights are forced to sum to zero (a long/short book with
        no net market exposure).
    gross_leverage:
        Cap on ``sum(|w|)``. The final weights are scaled so their gross
        exposure equals this value (``1.0`` = fully invested, no leverage).
        ``None`` leaves gross exposure untouched.
    long_only:
        If ``True``, negative weights are clipped to zero before scaling.
        Mutually exclusive with ``dollar_neutral`` (a dollar-neutral book must
        hold shorts); requesting both raises ``ValueError`` at use time.
    factor_neutral:
        If ``True``, alphas are residualised against the factor exposures
        before optimisation (see :meth:`PortfolioOptimizer.neutralize`).
    """

    dollar_neutral: bool = False
    gross_leverage: float | None = 1.0
    long_only: bool = False
    factor_neutral: bool = False


def risk_contributions(weights: FloatArray, cov: FloatArray) -> FloatArray:
    """Percentage risk contribution of each asset to total portfolio variance.

    The risk contribution of asset ``i`` is ``w_i * (cov @ w)_i``; these sum to
    the portfolio variance ``w' cov w``. Returned values are normalised to sum
    to 1 (so they read as fractions of total risk).
    """
    w = np.asarray(weights, dtype=float)
    sigma_w = cov @ w
    contrib = w * sigma_w
    total = contrib.sum()
    if total == 0.0:
        return np.zeros_like(contrib)
    return cast(FloatArray, contrib / total)


class PortfolioOptimizer:
    """Produce target weights from alpha scores and a covariance estimate.

    Two construction methods are provided:

    - :meth:`mean_variance` — closed-form, ridge-regularised mean–variance with
      a turnover / transaction-cost penalty that anchors the solution to the
      current book and so reduces churn.
    - :meth:`risk_parity` — iterative equal-risk-contribution weights, ignoring
      alphas (a pure risk allocation).

    Both apply the same :class:`Constraints` post-processing (dollar-neutrality,
    gross-leverage scaling, optional long-only clipping).
    """

    def __init__(self, *, risk_aversion: float = 1.0) -> None:
        """Initialise the optimiser.

        Parameters
        ----------
        risk_aversion:
            The ``lambda`` trade-off coefficient in the mean–variance
            objective ``alpha' w - lambda * w' cov w``. Higher values penalise
            risk more and produce smaller, more diversified books.
        """
        if risk_aversion <= 0.0:
            raise ValueError("risk_aversion must be positive")
        self.risk_aversion = risk_aversion

    # -- alpha conditioning ------------------------------------------------ #
    @staticmethod
    def neutralize(alphas: FloatArray, exposures: FloatArray) -> FloatArray:
        """Residualise alphas against factor exposures (factor-neutral alpha).

        Regresses ``alphas`` on the exposure matrix ``B`` (with an intercept)
        and returns the residuals, so the resulting signal is orthogonal to the
        factors — a long/short book built on it carries no intended factor bet.

        Uses a least-squares solve (``np.linalg.lstsq``), robust to collinear
        or rank-deficient exposures.
        """
        a = np.asarray(alphas, dtype=float)
        b = np.asarray(exposures, dtype=float)
        if b.ndim != 2 or b.shape[0] != a.shape[0]:
            raise ValueError("exposures must be (n_assets x n_factors) matching alphas")
        design = np.column_stack([np.ones(b.shape[0]), b])
        coef, *_ = np.linalg.lstsq(design, a, rcond=None)
        fitted = design @ coef
        return cast(FloatArray, a - fitted)

    # -- mean-variance ----------------------------------------------------- #
    def mean_variance(
        self,
        alphas: FloatArray,
        cov: FloatArray,
        *,
        constraints: Constraints | None = None,
        current_weights: FloatArray | None = None,
        cost_penalty: float = 0.0,
        exposures: FloatArray | None = None,
    ) -> FloatArray:
        """Closed-form mean–variance weights with a turnover penalty.

        Solves the regularised objective

        .. math::
            \\max_w \\; \\alpha^\\top w - \\lambda\\, w^\\top \\Sigma w
                     - \\tau\\, \\lVert w - w_0 \\rVert_2^2

        whose unconstrained optimum is the linear solve

        .. math::
            (2\\lambda \\Sigma + 2\\tau I)\\, w = \\alpha + 2\\tau w_0 .

        The ``cost_penalty`` term ``tau`` (a quadratic transaction-cost /
        turnover proxy) both regularises the solve — guaranteeing an invertible
        system even for a singular ``cov`` — and anchors the answer to
        ``current_weights``, which is what reduces churn relative to the
        unpenalised optimum. Constraints are then applied as post-processing.

        Parameters
        ----------
        alphas:
            Expected-return / alpha score per asset, shape ``(n_assets,)``.
        cov:
            Covariance matrix ``(n_assets, n_assets)``.
        constraints:
            :class:`Constraints` to enforce. Defaults to dollar-neutral,
            gross-leverage 1.0.
        current_weights:
            The book you currently hold, used as the turnover anchor ``w_0``.
            Defaults to zeros.
        cost_penalty:
            Quadratic turnover penalty ``tau >= 0``.
        exposures:
            Factor-exposure matrix for neutralisation; required iff
            ``constraints.factor_neutral`` is set.

        Returns
        -------
        weights:
            Target weights ``(n_assets,)`` after constraint post-processing.
        """
        constraints = constraints or Constraints(dollar_neutral=True, gross_leverage=1.0)
        a = np.asarray(alphas, dtype=float).copy()
        sigma = np.asarray(cov, dtype=float)
        n = a.shape[0]
        if sigma.shape != (n, n):
            raise ValueError("cov shape does not match alphas")
        if cost_penalty < 0.0:
            raise ValueError("cost_penalty must be non-negative")

        if constraints.factor_neutral:
            if exposures is None:
                raise ValueError("factor_neutral=True requires exposures")
            a = self.neutralize(a, exposures)

        w0 = (
            np.zeros(n)
            if current_weights is None
            else np.asarray(current_weights, dtype=float)
        )

        lam = self.risk_aversion
        tau = float(cost_penalty)
        # A tiny ridge keeps a singular cov solvable even when tau == 0.
        ridge = 1e-10
        amat = 2.0 * lam * sigma + (2.0 * tau + ridge) * np.eye(n)
        rhs = a + 2.0 * tau * w0
        w = np.linalg.solve(amat, rhs)
        return self._apply_constraints(w, constraints)

    # -- risk parity ------------------------------------------------------- #
    def risk_parity(
        self,
        cov: FloatArray,
        *,
        constraints: Constraints | None = None,
        max_iter: int = 1000,
        tol: float = 1e-10,
    ) -> FloatArray:
        """Iterative equal-risk-contribution (risk-parity) weights.

        Finds long-only weights whose per-asset risk contributions
        (:func:`risk_contributions`) are all equal, via the standard
        cyclical-coordinate fixed-point iteration on

        .. math::
            w_i \\leftarrow w_i \\cdot
                \\frac{b_i}{(\\Sigma w)_i / (w^\\top \\Sigma w)} ,

        which converges for any positive-definite ``cov`` from a positive start.
        The raw risk-parity solution is long-only by construction; constraints
        are applied afterwards (note: ``dollar_neutral`` does not combine
        meaningfully with risk parity and raises if requested).

        Parameters
        ----------
        cov:
            Covariance matrix ``(n_assets, n_assets)``.
        constraints:
            Post-processing constraints. Defaults to gross-leverage 1.0.
        max_iter, tol:
            Iteration budget and convergence tolerance on the weight update.

        Returns
        -------
        weights:
            Risk-parity target weights ``(n_assets,)``.
        """
        constraints = constraints or Constraints(gross_leverage=1.0)
        if constraints.dollar_neutral:
            raise ValueError("risk_parity produces a long-only book; dollar_neutral is invalid")
        sigma = np.asarray(cov, dtype=float)
        n = sigma.shape[0]
        budget = np.full(n, 1.0 / n)  # equal risk budget

        w = np.full(n, 1.0 / n)
        for _ in range(max_iter):
            sigma_w = sigma @ w
            # Fixed-point update w_i <- b_i / (Sigma w)_i. Its stationary point
            # satisfies w_i (Sigma w)_i = b_i * const, i.e. each asset's risk
            # contribution equals its budget b_i — the ERC condition. The
            # sqrt damps the step for stable convergence on correlated cov.
            w_new = np.sqrt(w * budget / np.maximum(sigma_w, 1e-300))
            w_new = w_new / w_new.sum()
            if np.max(np.abs(w_new - w)) < tol:
                w = w_new
                break
            w = w_new
        return self._apply_constraints(w, constraints)

    # -- constraint post-processing --------------------------------------- #
    @staticmethod
    def _apply_constraints(weights: FloatArray, constraints: Constraints) -> FloatArray:
        w = np.asarray(weights, dtype=float).copy()
        if constraints.dollar_neutral and constraints.long_only:
            raise ValueError("dollar_neutral and long_only are mutually exclusive")

        if constraints.dollar_neutral:
            w = w - w.mean()
        if constraints.long_only:
            w = np.clip(w, 0.0, None)

        if constraints.gross_leverage is not None:
            if constraints.gross_leverage < 0.0:
                raise ValueError("gross_leverage must be non-negative")
            gross = np.abs(w).sum()
            if gross > 0.0:
                w = w * (constraints.gross_leverage / gross)
        return cast(FloatArray, w)
