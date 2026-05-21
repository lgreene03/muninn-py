# Contributing to muninn-py

Thank you for considering a contribution. `muninn-py` is the Python research SDK for the [Muninn](https://github.com/lgreene03/muninn) feature-computation platform.

Before opening a PR, please read:

1. [README.md](README.md) — project overview and quick-start.
2. [docs/ROADMAP.md](docs/ROADMAP.md) — phased plan; check whether your idea already has a home.

If your change conflicts with the design direction in the ROADMAP, **surface the conflict first** — open an issue or a draft PR describing the proposed direction change.

## Code of Conduct

This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) v2.1. By participating you agree to uphold it.

## Ways to Contribute

- **Bug reports.** Open an issue. Include: SDK version, Python version, Muninn server version, minimal reproduction, observed vs expected behaviour.
- **Feature requests.** Check the ROADMAP first. If still relevant, open an issue describing the use case — especially how it affects the research workflow — before writing code.
- **Documentation.** Doc-only PRs are welcome. Keeping examples runnable is load-bearing.
- **Code.** See below.

## Getting Started

```bash
git clone https://github.com/lgreene03/muninn-py.git
cd muninn-py

# Create a virtual environment (Python 3.10+)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install the SDK in editable mode with all dev dependencies
pip install -e ".[dev]"

# Lint
ruff check .

# Type-check
mypy src/muninn

# Run the test suite
pytest -ra
```

You also need a running Muninn server for integration tests and notebook execution. The fastest way:

```bash
cd ../muninn   # the server repo
docker compose up -d
```

## Code Contributions

### 1. Open an issue first

For anything larger than a typo fix, open an issue describing:

- The problem or use case.
- The proposed approach.
- Which ROADMAP phase it belongs to.
- What tests will cover it.

This avoids wasted work and ensures alignment with the research-SDK design.

### 2. Branch and commit

```bash
git checkout -b feat/short-description
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(client): add streaming_features() method for WS endpoint
fix(cache): don't cache 4xx responses
docs(notebook): fix stale endpoint in alpha-backtest demo
```

Each commit should leave the test suite green.

### 3. Test coverage

All public methods require at least one unit test. For changes that touch the HTTP client or pydantic models, add a test in `tests/` using `httpx.MockTransport` (see existing tests for the pattern).

If you add a new SDK method, also update `notebooks/alpha_backtest_demo.ipynb` if it affects the demo workflow.

### 4. Type annotations

All public functions and methods must have complete type annotations. `mypy src/muninn` must pass without errors.

### 5. Pull Request

- Keep PRs focused; one logical change per PR.
- Reference the issue number in the PR description.
- The CI suite (lint, type-check, tests) must be green before review.
- Draft PRs are fine for early feedback.

## Style

- `ruff` for linting and import sorting (configured in `pyproject.toml`).
- `mypy` strict mode for public API.
- Docstrings on public classes and functions: one-line summary, then a blank line, then parameters and return if non-obvious.
- No docstrings on private helpers or test functions.

## Release Process

See [docs/RELEASING.md](docs/RELEASING.md).
