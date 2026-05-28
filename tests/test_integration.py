"""Testcontainers integration tests — boots the real Muninn stack and exercises every SDK method.

This is the single most important test in the SDK: it proves that the Python
client and the Java server actually agree on the HTTP contract, JSON shapes,
and semantic behaviour.  Unit tests (respx mocks) verify SDK logic in
isolation; this file verifies the *system*.

Run with::

    pytest -m integration tests/test_integration.py -v

Requires Docker and roughly 60 seconds for first cold start (image pulls).
Subsequent runs reuse cached images and finish in ~20 s.

Architecture
------------
The test spins up the full Muninn infrastructure via ``docker compose`` using
the compose file in the sibling ``muninn`` repo, then builds and boots the
Muninn server container on the same Docker network.  A synthetic trade event
is pushed through the ingestion REST endpoint so the feature engine has data
to compute against.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from muninn import (
    AsyncMuninnClient,
    MuninnClient,
    MuninnNotFoundError,
)
from muninn.models import (
    FeatureDefinition,
    ReplayJob,
    ReplayJobStatus,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The Muninn Java server repository — expected to be a sibling checkout.
MUNINN_SERVER_DIR = Path(os.environ.get("MUNINN_SERVER_DIR", Path(__file__).resolve().parents[2] / "muninn"))
COMPOSE_FILE = MUNINN_SERVER_DIR / "docker-compose.yml"
DOCKERFILE = MUNINN_SERVER_DIR / "Dockerfile"


def _compose_exists() -> bool:
    return COMPOSE_FILE.is_file() and DOCKERFILE.is_file()


# ---------------------------------------------------------------------------
# pytest markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _compose_exists(),
        reason=f"Muninn server repo not found at {MUNINN_SERVER_DIR}",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find an ephemeral port that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, *, timeout: float = 120, interval: float = 2) -> None:
    """Poll *url* until it returns a 2xx, or raise after *timeout* seconds."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code < 300:
                return
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as exc:
            last_err = exc
        time.sleep(interval)
    raise TimeoutError(
        f"Service at {url} did not become healthy within {timeout}s"
        + (f" (last error: {last_err})" if last_err else "")
    )


def _push_synthetic_trade(
    base_url: str,
    *,
    instrument: str = "BTC-USDT",
    price: str = "60000.00",
    quantity: str = "1.5",
    event_time: str | None = None,
) -> dict[str, Any]:
    """Push a single synthetic trade event through the Muninn ingestion API.

    Returns the JSON response from the server (usually the created event envelope).
    """
    if event_time is None:
        event_time = datetime.now(timezone.utc).isoformat()

    payload = {
        "eventType": "tradeEvent",
        "eventTime": event_time,
        "partitionKey": instrument,
        "source": "sdk-integration-test",
        "payload": {
            "instrument": instrument,
            "price": price,
            "quantity": quantity,
            "side": "BUY",
            "tradeId": str(uuid.uuid4()),
        },
    }
    resp = httpx.post(
        f"{base_url}/api/v1/events",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Docker Compose lifecycle (session-scoped)
# ---------------------------------------------------------------------------


def _run(cmd: str, *, cwd: Path | None = None, check: bool = True) -> str:
    """Run a shell command and return stdout. Raises on non-zero exit."""
    import subprocess

    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {cmd}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout.strip()


def _wait_for_service_health(
    service: str, *, cwd: Path, timeout: float = 120, interval: float = 2
) -> None:
    """Poll until *service*'s container reports a healthy Docker healthcheck.

    Used in place of ``docker compose up --wait``: the Muninn compose file ships
    a one-shot ``minio-init`` bucket-creator that exits 0 once the buckets exist,
    and ``--wait`` mis-reports that clean exit as a stack failure. We boot the
    stack detached and wait on the long-running services' healthchecks directly.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cid = _run(
            f"docker compose -f {COMPOSE_FILE} ps -q {service}",
            cwd=cwd,
            check=False,
        )
        if cid:
            status = _run(
                "docker inspect -f '{{.State.Health.Status}}' " + cid,
                check=False,
            )
            if status == "healthy":
                return
        time.sleep(interval)
    raise TimeoutError(f"Service {service!r} did not become healthy within {timeout}s")


@pytest.fixture(scope="session")
def compose_stack() -> Generator[dict[str, Any], None, None]:
    """Boot the full Muninn infrastructure + server via ``docker compose``.

    Yields a dict with connection details::

        {
            "muninn_url": "http://localhost:<port>",
            "postgres_port": <int>,
            "redpanda_port": <int>,
            "minio_port": <int>,
        }

    Tears everything down after the session.
    """
    from testcontainers.compose import DockerCompose

    # A unique project name avoids collisions if multiple test runs overlap.
    # testcontainers 4.x dropped the project_name kwarg — propagate it via the
    # COMPOSE_PROJECT_NAME env var, which docker compose honours and which
    # produces the same `{project_name}_default` network name we rely on below.
    project_name = f"muninn_integ_{uuid.uuid4().hex[:8]}"
    os.environ["COMPOSE_PROJECT_NAME"] = project_name

    # ---- 1. Boot infrastructure (Postgres, Redpanda, MinIO) ---------------

    # Boot detached rather than with ``--wait``: the Muninn compose file includes
    # a one-shot ``minio-init`` service that creates the MinIO buckets and exits 0.
    # ``docker compose up --wait`` treats that clean exit as a stack failure
    # (nothing depends on it via ``service_completed_successfully``), so we start
    # detached and wait on the long-running services' healthchecks ourselves.
    compose = DockerCompose(
        str(MUNINN_SERVER_DIR),
        compose_file_name="docker-compose.yml",
        wait=False,
    )
    compose.start()

    for _svc in ("postgres", "redpanda", "minio"):
        _wait_for_service_health(_svc, cwd=MUNINN_SERVER_DIR)

    # Resolve the host-side ports that Docker assigned. The compose file
    # maps fixed host ports (5433, 19092, 9002), but in CI those may clash.
    # We read the actual ports from docker-compose ps output.
    postgres_port = int(
        compose.get_service_port("postgres", 5432)
    )
    redpanda_port = int(
        compose.get_service_port("redpanda", 19092)
    )
    minio_port = int(
        compose.get_service_port("minio", 9000)
    )

    # ---- 2. Build and run the Muninn server container on the same network -

    muninn_host_port = _free_port()
    network_name = f"{project_name}_default"
    container_name = f"{project_name}_muninn_server"

    # Build the server image from the Dockerfile.
    _run(
        f"docker build -t {project_name}-muninn:latest -f {DOCKERFILE} .",
        cwd=MUNINN_SERVER_DIR,
    )

    # Run the server container, connected to the compose network so it can
    # reach postgres/redpanda/minio by their service names.
    _run(
        f"docker run -d --name {container_name} "
        f"--network {network_name} "
        f"-p {muninn_host_port}:8080 "
        f"-e SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/muninn "
        f"-e SPRING_DATASOURCE_USERNAME=muninn "
        f"-e SPRING_DATASOURCE_PASSWORD=muninn "
        f"-e SPRING_KAFKA_BOOTSTRAP_SERVERS=redpanda:9092 "
        f"-e MUNINN_STORAGE_S3_ENDPOINT=http://minio:9000 "
        f"-e MUNINN_STORAGE_S3_ACCESS_KEY=minioadmin "
        f"-e MUNINN_STORAGE_S3_SECRET_KEY=minioadmin "
        f"-e MUNINN_INGESTION_BINANCE_ENABLED=false "
        f"-e MUNINN_FEATURES_ENGINE_ENABLED=true "
        f"-e MUNINN_FEATURES_ENGINE_CODE_VERSION=integ-test "
        f"{project_name}-muninn:latest",
    )

    muninn_url = f"http://localhost:{muninn_host_port}"

    try:
        # Wait for the server's health endpoint to return 200.
        _wait_for_http(f"{muninn_url}/actuator/health", timeout=120)

        yield {
            "muninn_url": muninn_url,
            "postgres_port": postgres_port,
            "redpanda_port": redpanda_port,
            "minio_port": minio_port,
            "project_name": project_name,
        }
    finally:
        # ---- Teardown: stop server container, then compose stack ----------
        _run(f"docker rm -f {container_name}", check=False)
        _run(f"docker rmi {project_name}-muninn:latest", check=False)
        compose.stop()


@pytest.fixture(scope="session")
def muninn_url(compose_stack: dict[str, Any]) -> str:
    """The base URL of the running Muninn server."""
    return compose_stack["muninn_url"]


@pytest.fixture(scope="session")
def seeded_stack(compose_stack: dict[str, Any]) -> dict[str, Any]:
    """Push synthetic trade data so the server has features to serve.

    Pushes a handful of trades across a known time window. The feature engine
    should pick them up and compute windowed features (vwap, etc.) that the
    SDK can later query.
    """
    url = compose_stack["muninn_url"]
    base_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    # Push 10 trades spread across a 10-minute window.
    for i in range(10):
        ts = base_time.replace(minute=i)
        price = 60000 + i * 10
        _push_synthetic_trade(
            url,
            instrument="BTC-USDT",
            price=f"{price}.00",
            quantity="1.0",
            event_time=ts.isoformat(),
        )

    # Give the feature engine a few seconds to process the trades.
    time.sleep(5)
    return compose_stack


# ---------------------------------------------------------------------------
# Sync client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client(muninn_url: str) -> Generator[MuninnClient, None, None]:
    with MuninnClient(host=muninn_url, timeout=30.0) as c:
        yield c


@pytest.fixture
def seeded_client(seeded_stack: dict[str, Any]) -> Generator[MuninnClient, None, None]:
    with MuninnClient(host=seeded_stack["muninn_url"], timeout=30.0) as c:
        yield c


# ===========================================================================
# Test classes — one per SDK method surface
# ===========================================================================


class TestListFeatures:
    """``MuninnClient.list_features()`` → ``GET /api/v1/features``."""

    def test_returns_list_of_feature_definitions(self, client: MuninnClient) -> None:
        features = client.list_features()
        assert isinstance(features, list)
        # The server should have at least one registered feature definition
        # from its Flyway migrations / bootstrap config.
        for f in features:
            assert isinstance(f, FeatureDefinition)
            assert f.name  # non-empty string
            assert f.version

    def test_feature_definitions_have_expected_fields(self, client: MuninnClient) -> None:
        features = client.list_features()
        if not features:
            pytest.skip("Server has no registered features — cannot verify fields")
        defn = features[0]
        # These fields must be present per the OpenAPI spec.
        assert isinstance(defn.name, str)
        assert isinstance(defn.version, str)


class TestGetFeature:
    """``MuninnClient.get_feature()`` → ``GET /api/v1/features/{featureName}``."""

    def test_returns_polars_dataframe(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_feature(
            features[0].name,
            instrument="BTC-USDT",
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        assert isinstance(df, pl.DataFrame)

    def test_dataframe_has_expected_columns(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_feature(
            features[0].name,
            instrument="BTC-USDT",
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        if df.is_empty():
            pytest.skip("No feature values in the queried window")

        expected_cols = {"event_time", "window_start", "window_end", "value", "feature_name"}
        assert expected_cols.issubset(set(df.columns)), (
            f"Missing columns: {expected_cols - set(df.columns)}"
        )

    def test_dataframe_sorted_by_event_time(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_feature(
            features[0].name,
            instrument="BTC-USDT",
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        if df.height < 2:
            pytest.skip("Need at least 2 rows to verify sort order")

        times = df["event_time"].to_list()
        assert times == sorted(times), "DataFrame should be sorted ascending by event_time"

    def test_nonexistent_feature_raises_not_found(self, client: MuninnClient) -> None:
        with pytest.raises(MuninnNotFoundError):
            client.get_feature(
                "this_feature_does_not_exist_xyz",
                instrument="BTC-USDT",
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )

    def test_limit_parameter_caps_rows(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_feature(
            features[0].name,
            instrument="BTC-USDT",
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
            limit=2,
        )
        assert df.height <= 2


class TestGetFeatures:
    """``MuninnClient.get_features()`` — multi-feature fan-out + join."""

    def test_returns_joined_dataframe(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if len(features) < 2:
            pytest.skip("Need at least 2 features to test get_features join")

        names = [f.name for f in features[:2]]
        df = seeded_client.get_features(
            instrument="BTC-USDT",
            features=names,
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        assert isinstance(df, pl.DataFrame)
        # Each feature name should appear as a column.
        for name in names:
            assert name in df.columns, f"Expected column '{name}' in joined DataFrame"

    def test_single_feature_works(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_features(
            instrument="BTC-USDT",
            features=[features[0].name],
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        assert isinstance(df, pl.DataFrame)
        assert "event_time" in df.columns

    def test_serial_mode(self, seeded_client: MuninnClient) -> None:
        """``parallel=False`` should produce the same result."""
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_features(
            instrument="BTC-USDT",
            features=[features[0].name],
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
            parallel=False,
        )
        assert isinstance(df, pl.DataFrame)

    def test_empty_features_raises(self, client: MuninnClient) -> None:
        with pytest.raises(ValueError, match="at least one feature"):
            client.get_features(
                instrument="BTC-USDT",
                features=[],
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )


class TestGetPanel:
    """``MuninnClient.get_panel()`` — multi-instrument, multi-feature."""

    def test_returns_panel_with_instrument_column(self, seeded_client: MuninnClient) -> None:
        features = seeded_client.list_features()
        if not features:
            pytest.skip("No features registered on server")

        df = seeded_client.get_panel(
            instruments=["BTC-USDT"],
            features=[features[0].name],
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        assert isinstance(df, pl.DataFrame)
        assert "instrument" in df.columns
        assert "event_time" in df.columns

    def test_empty_instruments_raises(self, client: MuninnClient) -> None:
        with pytest.raises(ValueError, match="at least one instrument"):
            client.get_panel(
                instruments=[],
                features=["vwap.1m"],
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )

    def test_empty_features_raises(self, client: MuninnClient) -> None:
        with pytest.raises(ValueError, match="at least one feature"):
            client.get_panel(
                instruments=["BTC-USDT"],
                features=[],
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )


class TestListReplayJobs:
    """``MuninnClient.list_replay_jobs()`` → ``GET /api/v1/replay/jobs``."""

    def test_returns_list(self, client: MuninnClient) -> None:
        jobs = client.list_replay_jobs()
        assert isinstance(jobs, list)
        for job in jobs:
            assert isinstance(job, ReplayJob)


class TestGetReplayJob:
    """``MuninnClient.get_replay_job()`` → ``GET /api/v1/replay/jobs/{jobId}``."""

    def test_nonexistent_job_raises_not_found(self, client: MuninnClient) -> None:
        fake_id = str(uuid.uuid4())
        with pytest.raises(MuninnNotFoundError):
            client.get_replay_job(fake_id)


class TestSubmitReplayJob:
    """``MuninnClient.submit_replay_job()`` → ``POST /api/v1/replay/jobs``."""

    def test_submit_creates_pending_job(self, client: MuninnClient) -> None:
        job = client.submit_replay_job(
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:10:00Z",
        )
        assert isinstance(job, ReplayJob)
        assert job.status in (ReplayJobStatus.PENDING, ReplayJobStatus.RUNNING)
        assert job.job_id is not None

    def test_submitted_job_appears_in_list(self, client: MuninnClient) -> None:
        job = client.submit_replay_job(
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:05:00Z",
        )
        jobs = client.list_replay_jobs()
        job_ids = {j.job_id for j in jobs}
        assert job.job_id in job_ids, "Newly submitted job should appear in list_replay_jobs()"

    def test_get_replay_job_round_trip(self, client: MuninnClient) -> None:
        """Submit a job, then fetch it by ID — the two representations must agree."""
        submitted = client.submit_replay_job(
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:05:00Z",
        )
        fetched = client.get_replay_job(submitted.job_id)
        assert fetched.job_id == submitted.job_id
        assert fetched.range_from == submitted.range_from
        assert fetched.range_to == submitted.range_to

    def test_submit_with_topics(self, client: MuninnClient) -> None:
        job = client.submit_replay_job(
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:05:00Z",
            topics=["events.trade"],
        )
        assert isinstance(job, ReplayJob)
        assert "events.trade" in job.topics

    def test_submit_with_feature_version(self, client: MuninnClient) -> None:
        job = client.submit_replay_job(
            start="2026-01-15T12:00:00Z",
            end="2026-01-15T12:05:00Z",
            feature_version="v1",
        )
        assert isinstance(job, ReplayJob)
        assert job.feature_version == "v1"


# ===========================================================================
# Async client tests
# ===========================================================================


class TestAsyncClient:
    """Mirror core sync tests using ``AsyncMuninnClient``."""

    async def test_list_features(self, muninn_url: str) -> None:
        async with AsyncMuninnClient(host=muninn_url, timeout=30.0) as client:
            features = await client.list_features()
            assert isinstance(features, list)

    async def test_get_feature(self, seeded_stack: dict[str, Any]) -> None:
        url = seeded_stack["muninn_url"]
        async with AsyncMuninnClient(host=url, timeout=30.0) as client:
            features = await client.list_features()
            if not features:
                pytest.skip("No features registered on server")

            df = await client.get_feature(
                features[0].name,
                instrument="BTC-USDT",
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )
            assert isinstance(df, pl.DataFrame)

    async def test_list_replay_jobs(self, muninn_url: str) -> None:
        async with AsyncMuninnClient(host=muninn_url, timeout=30.0) as client:
            jobs = await client.list_replay_jobs()
            assert isinstance(jobs, list)

    async def test_submit_replay_job(self, muninn_url: str) -> None:
        async with AsyncMuninnClient(host=muninn_url, timeout=30.0) as client:
            job = await client.submit_replay_job(
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:10:00Z",
            )
            assert isinstance(job, ReplayJob)
            assert job.status in (ReplayJobStatus.PENDING, ReplayJobStatus.RUNNING)

    async def test_get_replay_job_round_trip(self, muninn_url: str) -> None:
        async with AsyncMuninnClient(host=muninn_url, timeout=30.0) as client:
            submitted = await client.submit_replay_job(
                start="2026-01-15T12:00:00Z",
                end="2026-01-15T12:05:00Z",
            )
            fetched = await client.get_replay_job(submitted.job_id)
            assert fetched.job_id == submitted.job_id


# ===========================================================================
# Health / smoke
# ===========================================================================


class TestServerHealth:
    """Verify the server is up before running heavier tests."""

    def test_health_endpoint(self, muninn_url: str) -> None:
        resp = httpx.get(f"{muninn_url}/actuator/health", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "UP"

    def test_api_docs_endpoint(self, muninn_url: str) -> None:
        """The OpenAPI spec endpoint should be reachable."""
        resp = httpx.get(f"{muninn_url}/api-docs", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert "paths" in body
