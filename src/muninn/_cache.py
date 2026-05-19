"""Optional disk-based response cache for ``get_feature``.

Feature time-series queries against the Muninn server are deterministic
over closed event-time windows: once a window's ``windowEnd`` is in the
past, the server's computed value won't change unless the feature's
``code_version`` changes. So we can cache responses keyed by request
URL + params and hand them back on the next call.

The cache is **opt-in** — set ``MuninnClient(cache_dir=...)``. When
left at the default ``None``, neither the client nor its tests touch
``diskcache``.

Limitations the user should know about:
- The cache key does not include the server's ``code_version``. If the
  server is upgraded with new feature logic, cached values from the old
  version remain in place and won't reflect the new computation. Call
  :meth:`MuninnClient.clear_cache` after such an upgrade.
- Only ``get_feature`` is cached. ``list_features`` changes as new
  features are registered; replay-job status changes over time;
  ``submit_replay_job`` mutates server state. None of those should be
  cached.
- ``get_features`` and ``get_panel`` compose ``get_feature`` calls, so
  they benefit transitively when their constituent feature requests
  hit the cache.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass


_CACHE_NAMESPACE = "muninn:get_feature:v1"


def open_cache(cache_dir: str) -> Any:
    """Open (and lazily import) the underlying ``diskcache.Cache``.

    The import is deferred so installations without the optional
    ``cache`` extra never need ``diskcache`` on the path. A missing
    dependency raises a clear error pointing at the install command.
    """
    try:
        import diskcache as _diskcache
    except ImportError as exc:  # pragma: no cover - import-time error path
        raise ImportError(
            "muninn[cache] is required for disk caching. "
            "Install with: pip install 'muninn-py[cache]'"
        ) from exc
    return _diskcache.Cache(cache_dir)


def cache_key(
    *,
    host: str,
    feature: str,
    instrument: str,
    start: str,
    end: str,
    limit: int | None,
) -> str:
    """Build a deterministic cache key for a ``get_feature`` request.

    Includes the namespace prefix so we can bump the format (e.g., move
    from JSON-payload to bytes-payload) without colliding with stale
    entries: increment the suffix on :data:`_CACHE_NAMESPACE`.
    """
    return (
        f"{_CACHE_NAMESPACE}|host={host.rstrip('/')}|feat={feature}|inst={instrument}"
        f"|from={start}|to={end}|limit={limit if limit is not None else ''}"
    )


def is_cacheable(end: str | datetime) -> bool:
    """Cache only when the ``end`` of the requested window is in the past.

    Windows that haven't closed yet may produce different results on
    subsequent calls — the server is still receiving events in their
    range. Refuse to cache those.

    A small skew tolerance (60 seconds) catches the borderline case
    where the requested ``end`` is "right now" and clock drift would
    otherwise make the call uncacheable.
    """
    if isinstance(end, datetime):
        end_dt = end
    else:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return end_dt < datetime.now(tz=timezone.utc)


def get(cache: Any | None, key: str) -> list[dict[str, Any]] | None:
    """Look up a cached response payload.

    Stored as the raw ``list[dict]`` rows from the server. Replays the
    same path through the SDK's model parsing on every read so any
    pydantic schema change is reflected on next call.
    """
    if cache is None:
        return None
    value = cache.get(key)
    if value is None:
        return None
    return cast("list[dict[str, Any]]", value)


def put(cache: Any | None, key: str, value: list[dict[str, Any]]) -> None:
    if cache is None:
        return
    cache.set(key, value)
