"""muninn — Python research SDK for the Muninn event-native feature platform.

Pulls deterministic features (e.g., rolling VWAP, Order Book Imbalance, VPIN)
from a running Muninn ``query-api`` into Polars or Pandas DataFrames for
notebook-driven alpha research.

The companion infrastructure project lives at
https://github.com/lgreene03/muninn — read its
``docs/steering/DETERMINISTIC_REPLAY.md`` to understand the property this SDK
preserves end-to-end: any feature value you pull is reproducible by replaying
the same input events through the same code version.

Quickstart
----------
.. code-block:: python

    from muninn import MuninnClient

    with MuninnClient() as m:
        df = m.get_features(
            instrument="BTC-USDT",
            features=["vwap.1m", "obi", "vpin"],
            start="2026-05-10T14:00:00Z",
            end="2026-05-10T15:00:00Z",
        )
        df.head()
"""

from muninn import notebook
from muninn._version import __version__
from muninn.async_client import AsyncMuninnClient
from muninn.client import MuninnClient
from muninn.exceptions import (
    MuninnAPIError,
    MuninnError,
    MuninnNotFoundError,
    MuninnTimeoutError,
    MuninnValidationError,
)
from muninn.models import FeatureDefinition, FeatureValue, ReplayJob, ReplayJobStatus

__all__ = [
    "__version__",
    "MuninnClient",
    "AsyncMuninnClient",
    "MuninnError",
    "MuninnAPIError",
    "MuninnNotFoundError",
    "MuninnTimeoutError",
    "MuninnValidationError",
    "FeatureValue",
    "FeatureDefinition",
    "ReplayJob",
    "ReplayJobStatus",
    "notebook",
]
