# muninn-py — Roadmap

Phased delivery, mirroring the discipline of the [server-side ROADMAP](https://github.com/lgreene03/muninn/blob/main/docs/steering/ROADMAP.md). Each phase ends with a working, tested, documented increment. Phases are not skipped.

## Phase A — Foundations ✅

**Goal.** Bootstrap the SDK as a usable, tested, releasable Python package.

**Delivered.**
- `MuninnClient` — synchronous `httpx`-backed client with context-manager lifecycle.
- Pydantic v2 models mirroring the server's contracts: `FeatureValue`, `FeatureDefinition`, `ReplayJob`, `ReplayJobSubmission`, `ReplayJobStatus`.
- Typed exception hierarchy: `MuninnError` → `MuninnAPIError` → `MuninnNotFoundError` / `MuninnValidationError`; `MuninnTimeoutError`.
- `get_feature`, `get_features` (outer/inner join on `event_time`), `list_features`, `list_replay_jobs`, `get_replay_job`, `submit_replay_job`.
- 24 unit tests using `respx` to mock the Muninn HTTP API.
- Bundled `notebooks/alpha_backtest_demo.ipynb` — pull features, compute forward returns, IC, plot, submit a replay.
- `pyproject.toml` (setuptools backend, PEP 621), `ruff`, `mypy --strict` with the pydantic plugin, pytest config.
- GitHub Actions CI matrix on Python 3.10 / 3.11 / 3.12 — green on first push.
- Apache 2.0 license, Dependabot, `.github/workflows/release.yml` for PyPI Trusted Publishing (OIDC).
- `docs/RELEASING.md` documenting the one-time PyPI trusted-publisher setup.

**Exit criteria.** `pip install -e .` works locally and CI is green. _Met as of `1d7f605`._

---

## Phase B — Researcher Ergonomics ✅

**Goal.** Make the SDK pleasant to use in the workflows quants actually run.

**Delivered.**
- ✅ **`AsyncMuninnClient`** — async sibling using `httpx.AsyncClient`. Same surface, same exceptions.
- ✅ **Parallel `get_features`** — async fans out via `asyncio.gather`; sync fans across a thread pool (opt out with `parallel=False`).
- ✅ **`get_panel(instruments=[...], features=[...])`** — multi-instrument, multi-feature in one call. Returns a long-form Polars frame keyed by `(instrument, event_time)`.
- ✅ **`muninn.notebook` helpers** — `forward_returns`, `information_coefficient`, `rolling_corr`, `hit_rate`. Polars in, Polars out, no mutation, no wall-clock reads.
- ✅ **`muninn` CLI** — Click-based. `muninn features list`, `muninn features get`, `muninn replay submit / status / list`. JSON or table output.
- ✅ **`.pandas` accessor on both clients** — same surface, returns `pandas.DataFrame`. Pyarrow-free conversion so no hard dependency added.

**Exit criteria met.** A researcher can pull a 5-feature, 3-instrument panel and compute IC against forward returns in fewer than 10 lines of notebook code. The async + sync parallel paths both eliminate the serial latency cost of multi-feature fetches. **72 unit tests, all green** on every supported Python.

---

## Phase C — Production Readiness ✅

**Goal.** Survive flaky networks, expensive queries, and long-running notebook sessions.

**Delivered.**
- ✅ **Retry with exponential backoff** via `RetryConfig(max_attempts, initial_backoff, max_backoff, backoff_factor, jitter, retry_statuses)`. Retries the configured 5xx and a small list of transport exceptions; never retries 4xx or already-decoded responses. Sync and async paths share policy. Disable with `max_attempts=1`.
- ✅ **Per-operation timeouts.** `MuninnClient(timeout=httpx.Timeout(connect=, read=, write=, pool=))` alongside the existing `float`.
- ✅ **Optional disk cache.** `pip install 'muninn-py[cache]'` then `MuninnClient(cache_dir="~/.muninn/cache")`. Caches only closed event-time windows. `client.clear_cache()` drops everything. Cache survives process restart.
- ✅ **Connection-pool tunables.** `max_connections`, `max_keepalive_connections`, `keepalive_expiry`.

**Exit criteria met.** A 5-minute notebook reload with the same query range hits the cache instead of re-fetching. A server returning a 503 once doesn't blow up the notebook session. **95 unit tests** across 12 source files, all green on Python 3.10 / 3.11 / 3.12.

---

## Phase D — Quality ✅

**Goal.** Every claim the SDK README makes is verified by a test that runs on every PR.

**Deliverables.**
- ✅ **Testcontainers integration test.** `tests/test_integration.py` — 27 tests that boot the full Muninn stack (PostgreSQL + Redpanda + MinIO + JVM server) via `testcontainers[compose]`, push synthetic trades through the ingestion API, and exercise every SDK method (sync + async): `list_features`, `get_feature`, `get_features`, `get_panel`, `list_replay_jobs`, `get_replay_job`, `submit_replay_job`. Session-scoped Docker lifecycle; `@pytest.mark.integration` marker excluded from default `pytest` runs. Run with `pytest -m integration -v`.
- ✅ **OpenAPI contract test.** A recorded spec snapshot at `tests/testdata/muninn_api_docs_v1.json` (re-recordable via `curl http://localhost:8080/api-docs`) is checked by `tests/test_openapi_contract.py` (15 tests). Asserts every endpoint path exists, required query params are declared, and camelCase response field names haven't been renamed server-side. No running server required — static offline test that runs on every PR. To re-sync with a live server: start Muninn locally and run `curl -s http://localhost:8080/api-docs | python -m json.tool > tests/testdata/muninn_api_docs_v1.json`.
- ✅ **Performance benchmarks** (`pytest-benchmark`). Baseline a 10K-row `get_feature`. CI fails on > 25 % regression once a baseline is committed (`.benchmarks/baseline.json`); see `tests/bench_client.py` for instructions.
- ✅ **Notebook execution in CI.** `.github/workflows/integration.yml` has a `notebook-execution` job that checks out the Muninn server, boots the full stack via Docker, seeds synthetic data, then runs both notebooks (`alpha_backtest_demo.ipynb` and `feature_drift_monitoring.ipynb`) via `jupyter nbconvert --execute`. Executed notebooks are uploaded as artifacts.

**Exit criteria.** Every public method has at least one contract test against a real server. The bundled notebook executes end-to-end in CI. _Met._

---

## Phase E — Polish and Distribute ✅

**Goal.** Be installable, discoverable, and explainable.

**Deliverables.**
- ✅ **Mkdocs documentation site.** Auto-rendered API reference (from docstrings + pydantic models), getting-started guide, notebook page. Hosted on GitHub Pages via `.github/workflows/docs.yml`.
- ✅ **A second example notebook** beyond alpha-backtest — `notebooks/feature_drift_monitoring.ipynb` walks through baseline-vs-observed distributional drift (Δmean in baseline σ, σ-ratio, p95 shift), KDE + time-series visualisation, code-version cohort grouping, and a replay-job throughput sanity check. The notebook source lives in `notebooks/_build_drift_notebook.py` so diffs review as Python instead of JSON; regenerate the `.ipynb` with `python notebooks/_build_drift_notebook.py`.
- ✅ **PyPI publish.** `v0.1.0` published to PyPI via Trusted Publishing (OIDC). `pip install muninn-py` works. Trusted Publisher setup steps in `docs/RELEASING.md`.
- ✅ **`CONTRIBUTING.md`** and **`SECURITY.md`** matching the server repo's discipline.
- ✅ **Cross-link from server's `companion-sdks` section** with the published doc-site URL. Muninn server README links to `https://lgreene03.github.io/muninn-py` alongside the repo URL.
- ✅ **Streamlit researcher dashboard** (`muninn dashboard`, behind the `[dashboard]` extra). Direction A of the four-repo customer-UI plan. Pages: feature explorer, forward-returns + IC, calibration-CSV viewer. The polish surface for "show this to a stakeholder and they get it in 5 minutes". Auth + multi-tenancy explicitly out of scope — that's Direction C, a different product.
- ✅ **SDK smoke test** (`scripts/smoke.sh`). Validates CLI commands and synthetic trade ingestion against a running Muninn server. Auto-boots the server from sibling repo if not already running.

**Exit criteria.** `pip install muninn-py` works. A new user reading the docs site for 15 minutes can do meaningful research against a running server. _Met._

---

## Phase G — Live streaming client (SSE) ✅ _promoted by T3_

**Goal.** Consume the muninn server's live feature stream so a researcher can watch features evolve in real time instead of polling the historical Query API. **Promoted out of Phase F when trigger T3 tripped** — the server shipped `GET /api/v1/features/stream` (muninn Phase 10 / [ADR-0009](https://github.com/lgreene03/muninn/blob/main/docs/adr/0009-streaming-features-sse.md)).

**Delivered.**
- ✅ **`MuninnStreamClient`** (sync) and **`AsyncMuninnStreamClient`** (async) in `muninn.streaming`. Each `stream(feature=None)` connects to `GET /api/v1/features/stream` (`text/event-stream`) and yields `FeatureValue`s as the engine produces them; the optional `feature=` filter restricts to one feature name. Both mirror the existing clients' construction (`host`, `timeout`, `headers`, connection-pool tunables) and context-manager lifecycle (`with` / `async with`).
- ✅ **SSE parsing** via a small incremental `_SseDecoder` (handles multi-line `data`, ignores keepalive comments and non-`feature` events). No new runtime dependency — pure `httpx.stream` + manual frame decode. The read timeout is disabled for the long-lived connection (connect/write/pool stay bounded); keepalives keep it warm.
- ✅ **Error mapping** reuses the shared `unwrap` path, so a rejected handshake raises the same `MuninnNotFoundError` / `MuninnValidationError` / `MuninnAPIError`. New `MuninnStreamError` covers malformed frames; `MuninnTimeoutError` covers connection timeout.
- ✅ **CLI** `muninn stream listen [--feature NAME] [--count N]` prints live events as newline-delimited JSON.
- ✅ **Exports** `MuninnStreamClient`, `AsyncMuninnStreamClient`, `MuninnStreamError` from the package root.
- ✅ **Tests.** `tests/test_streaming.py` (sync + async: frame parsing, filter passthrough, HTTP-error mapping, malformed-frame handling) and a `muninn stream listen` CLI test, all respx-mocked. Full suite green; ruff + mypy `--strict` clean.

**Exit criteria.** `for e in MuninnStreamClient().stream(feature="vwap.1m"): ...` yields live values sub-second after each window closes. _Met._ _Streaming docs page + a notebook example tracked as polish._

---

## Phase F — Future _(deferred / speculative)_

Tracked so ideas aren't lost; explicitly not scheduled. Each is gated by an **observable trigger** (never a date) catalogued in [sleipnir/docs/TRIGGERS.md](https://github.com/lgreene03/sleipnir/blob/main/docs/TRIGGERS.md), the shared cross-repo trigger catalog. When a trigger trips, the item moves out of Phase F into the next numbered phase, marked 🟢 with the trigger ID.

- ~~**WebSocket streaming client** — when the server adds a streaming features endpoint.~~ ✅ **Promoted by T3 → delivered as Phase G** (SSE, not WebSocket — the feed is push-only; see ADR-0009). See Phase G above.
- **`source` filter for multi-exchange awareness** — the server now ingests from multiple exchanges (ADR-0008 on the server side). The SDK could filter time-series by source tag. Wait for a real use case — features are canonical regardless of which exchange they came from, so the SDK doesn't have to care about sources by default. _Gated by **T15** (a researcher has a concrete analysis that requires per-exchange feature slicing)._
- **Auth helpers.** When operators front the server with reverse-proxy auth, a typed `MuninnClient(auth=BearerToken(...))` helper. Today the `headers={"Authorization": "..."}` escape hatch works; a typed helper is just polish. _Gated by **T14** (the server is fronted by reverse-proxy auth in a shared/multi-user deployment)._
- **Second-language client** — TypeScript for browser dashboards. Significant scope, no current driver. _Gated by **T13** (a browser dashboard is built that calls muninn directly)._

---

## Non-goals for the SDK

In the same spirit as the server's [NON_GOALS.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/NON_GOALS.md):

- **Not a backtesting framework.** It is a data-access library. Researchers compose it with whatever backtesting framework they prefer.
- **Not a trading client.** No orders, no execution, no portfolio state. The server isn't this either.
- **Not a feature-engineering library.** Features are computed deterministically on the server. The SDK transports them; it doesn't invent new ones.
- **Not multi-language.** Python-only. A TypeScript / Go / Rust client is a different project.
- **Not a real-time streaming client** until the server has a streaming endpoint to consume.
- **Not opinionated about plotting.** The notebook helpers compute values; the user picks the chart library.

---

## Phase ordering rationale

- **B before C** because ergonomics multiplies usage; production hardening is wasted on a tool nobody finds pleasant.
- **D before E** because publishing an unverified SDK is worse than not publishing at all. The Testcontainers test is the unlock for the PyPI tag.
- **F never on its own schedule** — items move out only when a real driver appears.
