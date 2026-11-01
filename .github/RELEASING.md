# Releasing

Releases are published by the [Publish](workflows/publish.yml) workflow, started by hand from the Actions tab on `main`. It runs the CI checks, builds the sdist and wheel, publishes them to TestPyPI and then PyPI through trusted publishing, and finally creates the `vX.Y.Z` tag and a GitHub release with the distributions attached.

The run form has two checkboxes, both on by default: **Publish to TestPyPI** and **Publish to PyPI**. With only TestPyPI checked the run stops there, with no PyPI upload, tag or release, which is how to rehearse a version. With only PyPI checked it skips TestPyPI. The version field is optional and defaults to `pyproject.toml`.

## One-time setup

1. On PyPI and on TestPyPI, add a pending trusted publisher (Account settings → Publishing):
   - Project name: `leis-mcp`
   - Owner: `mazulo`, repository: `leis-mcp`
   - Workflow name: `publish.yml`
   - Environment name: `pypi` on PyPI, `testpypi` on TestPyPI
2. On GitHub, create the `pypi` and `testpypi` environments (Settings → Environments). Restricting their deployment branches to `main` and adding yourself as a required reviewer on `pypi` is recommended.

No PyPI token is stored in the repository.

## Cutting a release

1. Bump `version` in `pyproject.toml` on `main` and push.
2. Actions → Publish → Run workflow (branch `main`). When the version field is filled it must match `pyproject.toml`.

The workflow refuses to run outside `main`, with neither checkbox selected, when the version does not match `pyproject.toml`, or when the tag already exists.
