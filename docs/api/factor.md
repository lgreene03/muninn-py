# Factor model & portfolio construction

The portfolio-construction half of the research SDK. Where the
[notebook helpers](notebook.md) get you from a feature panel to an alpha score
per asset, `muninn.factor` gets you from a panel of alpha scores plus a returns
history to a set of **target weights** you could trade — with a covariance
estimate that is well-conditioned enough to optimise against.

The module is numpy + pandas only (no scipy, no sklearn) and imports nothing
from the HTTP client, so it works on any returns matrix, not just one pulled
from Muninn.

!!! tip "Worked example"
    [`examples/ic_capacity_research.py`](https://github.com/lgreene03/muninn-py/blob/main/examples/ic_capacity_research.py)
    runs the whole pipeline end to end — features → alpha → IC → covariance →
    weights → backtest — on a fully offline synthetic panel.

::: muninn.factor.FactorModel

::: muninn.factor.PortfolioOptimizer

::: muninn.factor.Constraints

::: muninn.factor.ledoit_wolf_shrinkage

::: muninn.factor.risk_contributions
