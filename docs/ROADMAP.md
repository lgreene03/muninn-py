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

## Phase C — Production Readiness

**Goal.** Survive flaky networks, expensive queries, and long-running notebook sessions.

**Deliverables.**
- **Retry with backoff** on transient failures (5xx, connection reset). Configurable: `MuninnClient(retries=3, backoff="exponential")`.
- **Distinct connect/read/write/pool timeouts.** Today one global `timeout` covers everything; `httpx` supports the finer breakdown.
- **Optional disk-based response cache.** `MuninnClient(cache_dir="~/.muninn/cache")`. Feature time-series over closed event-time windows are deterministic — safe to cache indefinitely. Cache invalidation only by manual clear; the server's `code_version` already keys outputs.
- **Connection pool tunables** (`max_connections`, `keepalive_expiry`).

**Exit criteria.** A 5-minute notebook reload with the same query range hits the cache instead of re-fetching. A server returning a 503 once doesn't blow up the notebook session.

---

## Phase D — Quality

**Goal.** Every claim the SDK README makes is verified by a test that runs on every PR.

**Deliverables.**
- **Testcontainers integration test.** Boots the JVM Muninn server in CI, pushes synthetic trades through the ingestion API, exercises every SDK method against the real server. The single most important add — proves the SDK and server actually agree on the contract.
- **OpenAPI contract test.** Pull the server's `/api-docs` JSON and assert every endpoint the SDK calls exists with the parameters the SDK sends. Catches server-side breaking changes at PR time.
- **Performance benchmarks** (`pytest-benchmark`). Baseline a 10K-row `get_feature`. CI fails on > 25 % regression.
- **Notebook execution in CI.** `nbconvert --execute notebooks/alpha_backtest_demo.ipynb` against a Testcontainers Muninn server. The bundled demo can never silently break.

**Exit criteria.** Every public method has at least one contract test against a real server. The bundled notebook executes end-to-end in CI.

---

## Phase E — Polish and Distribute

**Goal.** Be installable, discoverable, and explainable.

**Deliverables.**
- **Mkdocs documentation site.** Auto-rendered API reference (from docstrings + pydantic models), tutorials, the demo notebook rendered as a page. Hosted on GitHub Pages.
- **A second example notebook** beyond alpha-backtest — likely "monitoring feature drift via replay-divergence metrics".
- **PyPI publish.** Trusted Publisher setup steps already in `docs/RELEASING.md`; this phase is the actual `v0.1.0` tag-and-publish.
- **`CONTRIBUTING.md`** and **`SECURITY.md`** matching the server repo's discipline.
- **Cross-link from server's `companion-sdks` section** with the published doc-site URL once Phase E ships.

**Exit criteria.** `pip install muninn-py` works. A new user reading the docs site for 15 minutes can do meaningful research against a running server.

---

## Phase F — Future _(deferred / speculative)_

Tracked so ideas aren't lost; explicitly not scheduled.

- **WebSocket streaming client** — when the server adds a streaming features endpoint. Not before.
- **`source` filter for multi-exchange awareness** — the server now ingests from multiple exchanges (ADR-0008 on the server side). The SDK could filter time-series by source tag. Wait for a real use case — features are canonical regardless of which exchange they came from, so the SDK doesn't have to care about sources by default.
- **Auth helpers.** When operators front the server with reverse-proxy auth, a typed `MuninnClient(auth=BearerToken(...))` helper. Today the `headers={"Authorization": "..."}` escape hatch works; a typed helper is just polish.
- **Second-language client** — TypeScript for browser dashboards. Significant scope, no current driver.

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
