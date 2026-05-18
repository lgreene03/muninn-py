# Releasing `muninn-py`

This SDK is published to PyPI via **Trusted Publishing** (OIDC) — no API tokens stored in repository secrets. The release workflow is `.github/workflows/release.yml`.

## One-time setup

These steps are done once by the maintainer before the first publish.

### 1. Reserve the project on PyPI and TestPyPI

- Sign in to https://pypi.org and reserve the name `muninn-py` (publish an initial `0.0.0` or use the "pending publisher" flow).
- Repeat at https://test.pypi.org so the staging flow has a target.

### 2. Add a "pending trusted publisher" on PyPI

Visit https://pypi.org/manage/account/publishing/ and add:

| Field | Value |
|---|---|
| PyPI Project Name | `muninn-py` |
| Owner | `lgreene03` |
| Repository name | `muninn-py` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Repeat the same on https://test.pypi.org with environment name `testpypi`.

### 3. Create the two GitHub environments

In the repo's Settings → Environments, create:

- **`pypi`** — production. Optionally add a required reviewer so a human approves the final upload.
- **`testpypi`** — staging. No reviewer required.

These environments are referenced by the workflow's `environment:` blocks. PyPI's OIDC verification checks that the workflow is running *under* the registered environment.

## Cutting a release

```bash
# From a clean main with green CI.

# 1. Decide the next version per docs/steering/VERSIONING.md (in the main muninn repo)
NEW=0.2.0

# 2. Bump in three places. Keep them aligned.
#    - pyproject.toml          [project].version
#    - src/muninn/_version.py  __version__
#    - CHANGELOG.md            promote [Unreleased] -> [NEW] — YYYY-MM-DD

# 3. Commit and tag
git commit -am "chore: release ${NEW}"
git tag -a "v${NEW}" -m "v${NEW}"
git push --follow-tags
```

The push of the tag triggers `release.yml`. It will:

1. Build the sdist and wheel.
2. Run `twine check` for metadata validity.
3. Verify the tag matches `pyproject.toml`'s `[project].version`.
4. Publish to PyPI under the `pypi` environment via OIDC.

After the workflow goes green, draft a GitHub Release on the tag with the CHANGELOG section as the body.

## Pre-release dry-run against TestPyPI

Run the workflow manually from the Actions tab with `target=testpypi`. The build step runs unconditionally; the TestPyPI publish job is gated on `inputs.target == 'testpypi'` and `skip-existing: true` is set so re-runs at the same version are safe.

## Yanking a release

If a published release is broken:

1. **Yank, don't delete.** PyPI yanks remove the version from `pip install` resolution but preserve audit history. Use the "Yank release" button on the project page.
2. Cut a patch release that supersedes it. Yanks should always be followed by a fix.

See [VERSIONING.md](https://github.com/lgreene03/muninn/blob/main/docs/steering/VERSIONING.md) on the main repo for the deeper rules.
