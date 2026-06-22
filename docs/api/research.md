# Alpha-research diagnostics

The signal-evaluation half of the research SDK: information coefficient, alpha
decay, signal half-life, and capacity analysis. These are the questions a quant
asks **before** committing capital to a signal — how well the alpha predicts
forward returns, at what horizon that power lives, how fast the signal
mean-reverts, and how much notional it can absorb before market impact eats the
edge.

Like [`muninn.factor`](factor.md), this module is numpy + pandas only and
self-contained — nothing here imports the HTTP client, so every function runs on
any returns/​signal arrays.

!!! tip "Worked example"
    [`examples/ic_capacity_research.py`](https://github.com/lgreene03/muninn-py/blob/main/examples/ic_capacity_research.py)
    demonstrates `ic_decay_curve`, `signal_half_life`, and the rest of this
    module on an offline synthetic panel.

::: muninn.research.ic

::: muninn.research.rank_ic

::: muninn.research.ic_decay_curve

::: muninn.research.ICResult

::: muninn.research.forward_returns

::: muninn.research.autocorrelation

::: muninn.research.signal_half_life

::: muninn.research.capacity_estimate

::: muninn.research.CapacityResult
