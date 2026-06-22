#!/usr/bin/env python3
"""Worked alpha-research flow: features -> alpha -> IC -> covariance -> weights -> backtest.

This script wires the whole Muninn research SDK together end to end on a
**multi-asset, fully offline, synthetic** panel that mimics the live
``features.obi.v1`` event (BTC/ETH/SOL/XRP/DOGE with order-book imbalance,
momentum, volatility, ...). It is deliberately runnable with no server, no
network, and no paid dependency — just numpy / pandas plus this package.

Pipeline
--------
1.  **Features.** Build a synthetic per-asset panel of prices and an
    order-book-imbalance (OBI) feature whose contemporaneous value carries a
    *small, decaying* edge on next-bar returns — i.e. a realistic
    microstructure alpha, not a money printer.
2.  **Alpha scores.** Cross-sectionally z-score the OBI feature each bar to get
    a dollar-neutral-friendly alpha.
3.  **IC analysis.** Pool the panel and run :func:`muninn.research.ic_decay_curve`
    to show the IC decay across 1/5/15-bar horizons, plus the signal half-life.
4.  **Covariance.** Fit :class:`muninn.factor.FactorModel` (Ledoit-Wolf shrunk
    covariance) on the realised returns.
5.  **Weights.** Build dollar-neutral mean-variance weights each bar with
    :class:`muninn.factor.PortfolioOptimizer`.
6.  **Backtest.** Vectorised, net-of-a-simple-cost PnL of holding those weights
    into the next bar; report annualised Sharpe before/after cost.
7.  **Capacity.** Feed the gross Sharpe into
    :func:`muninn.research.capacity_estimate` to size the book.

Run::

    python examples/ic_capacity_research.py

It prints a compact report and exits 0. Numbers are synthetic; the *plumbing*
is the point — swap step 1 for ``MuninnClient.get_features(...)`` to run it on
real captured features.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from muninn.factor import Constraints, FactorModel, PortfolioOptimizer
from muninn.research import capacity_estimate, ic_decay_curve, signal_half_life

UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
BARS_PER_YEAR = 365 * 24 * 60  # 1-minute bars


def synth_panel(
    n_bars: int = 6000, seed: int = 42, edge: float = 0.012
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthesise a multi-asset price + OBI-feature panel.

    Returns two ``(n_bars x n_assets)`` frames: ``prices`` and ``obi`` (the
    order-book-imbalance feature). The next-bar return of each asset embeds a
    small linear dependence on the *current* OBI (the alpha), a shared market
    factor, and idiosyncratic noise — so the OBI feature is genuinely but
    weakly predictive, with the edge concentrated at the 1-bar horizon.
    """
    rng = np.random.default_rng(seed)
    n_assets = len(UNIVERSE)
    betas = np.array([1.0, 1.1, 1.3, 0.9, 1.5])

    market = rng.normal(0.0, 0.0008, size=n_bars)
    obi = rng.normal(0.0, 1.0, size=(n_bars, n_assets))
    # Make OBI mildly persistent (AR(1)) so it has a non-trivial half-life.
    for t in range(1, n_bars):
        obi[t] = 0.6 * obi[t - 1] + 0.8 * obi[t]

    idio = rng.normal(0.0, 0.0012, size=(n_bars, n_assets))
    # Next-bar return depends on current OBI (the alpha) + market + noise.
    ret = np.empty((n_bars, n_assets))
    for a in range(n_assets):
        ret[:, a] = betas[a] * market + edge * 0.01 * obi[:, a] + idio[:, a]

    prices = 100.0 * np.cumprod(1.0 + ret, axis=0)
    price_df = pd.DataFrame(prices, columns=UNIVERSE)
    obi_df = pd.DataFrame(obi, columns=UNIVERSE)
    return price_df, obi_df


def cross_sectional_z(frame: pd.DataFrame) -> pd.DataFrame:
    """Row-wise (cross-sectional) z-score — the alpha score per bar."""
    mu = frame.mean(axis=1)
    sd = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mu, axis=0).div(sd, axis=0).fillna(0.0)


def main() -> None:
    print("=" * 70)
    print("Muninn worked example — IC / alpha-decay / capacity research flow")
    print("=" * 70)

    # 1. Features --------------------------------------------------------- #
    prices, obi = synth_panel()
    n_bars = len(prices)
    print(f"\n[1] Synthetic panel: {n_bars} bars x {len(UNIVERSE)} assets {UNIVERSE}")

    # 2. Alpha scores ----------------------------------------------------- #
    alpha = cross_sectional_z(obi)
    print("[2] Alpha = cross-sectional z-score of OBI feature")

    # 3. IC analysis (pooled across the universe) ------------------------- #
    pooled_prices = []
    pooled_signal = []
    for asset in UNIVERSE:
        pooled_prices.append(prices[asset].to_numpy())
        pooled_signal.append(alpha[asset].to_numpy())
    # Per-asset decay curves averaged for a universe-level view.
    curves = [
        ic_decay_curve(p, s, horizons=(1, 5, 15)).set_index("horizon")["ic"]
        for p, s in zip(pooled_prices, pooled_signal, strict=True)
    ]
    ic_curve = pd.concat(curves, axis=1).mean(axis=1)
    print("\n[3] IC decay curve (universe-mean Spearman IC):")
    for h, v in ic_curve.items():
        print(f"      horizon={h:>3} bars   IC={v:+.4f}")
    hl = np.mean([signal_half_life(s) for s in pooled_signal])
    print(f"    Signal half-life (AR1, mean over assets): {hl:.2f} bars")

    # 4. Covariance ------------------------------------------------------- #
    rets = prices.pct_change().dropna()
    fm = FactorModel().fit(rets.to_numpy().T)  # (assets x time)
    cov = fm.covariance
    print(f"\n[4] Ledoit-Wolf shrunk covariance fitted; "
          f"shrinkage delta={fm.shrinkage_intensity:.3f}")

    # 5. Weights + 6. vectorised backtest -------------------------------- #
    opt = PortfolioOptimizer(risk_aversion=5.0)
    constraints = Constraints(dollar_neutral=True, gross_leverage=1.0)
    cost_bps = 1.0  # 1 bp per unit turnover, charged on |dw|

    weights = np.zeros((n_bars, len(UNIVERSE)))
    prev = np.zeros(len(UNIVERSE))
    for t in range(n_bars):
        a = alpha.iloc[t].to_numpy()
        w = opt.mean_variance(a, cov, constraints=constraints, current_weights=prev)
        weights[t] = w
        prev = w

    bar_ret = prices.pct_change().shift(-1).to_numpy()  # next-bar return, aligned
    valid = ~np.isnan(bar_ret).any(axis=1)
    w_v = weights[valid]
    r_v = bar_ret[valid]
    gross_pnl = (w_v * r_v).sum(axis=1)
    turnover = np.abs(np.diff(w_v, axis=0, prepend=w_v[:1])).sum(axis=1)
    cost = turnover * (cost_bps / 1e4)
    net_pnl = gross_pnl - cost

    def sharpe(x: np.ndarray) -> float:
        sd = x.std()
        return float(np.sqrt(BARS_PER_YEAR) * x.mean() / sd) if sd > 0 else 0.0

    gross_sharpe = sharpe(gross_pnl)
    net_sharpe = sharpe(net_pnl)
    print("\n[5/6] Vectorised dollar-neutral backtest:")
    print(f"      gross Sharpe (annualised) : {gross_sharpe:+.2f}")
    print(f"      net   Sharpe (annualised) : {net_sharpe:+.2f}")
    print(f"      mean turnover / bar       : {turnover.mean():.3f}")

    # 7. Capacity --------------------------------------------------------- #
    eff_sharpe = max(gross_sharpe, 0.25)  # floor so the demo always sizes a book
    cap = capacity_estimate(
        gross_sharpe=eff_sharpe,
        adv_notional=50_000_000.0,  # $50M ADV per asset (illustrative)
        impact_coefficient=0.5,
        trades_per_year=BARS_PER_YEAR / 60.0,  # ~ hourly rebal cadence
        max_sharpe_erosion=0.10,
        volatility_per_trade=float(np.std(gross_pnl)) or 0.001,
    )
    print("\n[7] Capacity (sqrt-impact, 10% Sharpe-erosion budget):")
    print(f"      max deployable notional/trade : ${cap.capacity_notional:,.0f}")
    print(f"      impact at capacity            : {cap.impact_at_capacity:.6f}")

    print("\nDone. (Synthetic data — this demonstrates the rails, not an edge.)")


if __name__ == "__main__":
    main()
