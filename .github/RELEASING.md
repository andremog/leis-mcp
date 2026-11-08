# Releasing

Releases are published by the [Publish](workflows/publish.yml) workflow, started by hand from the Actions tab on `main`. It runs the CI checks, builds the sdist, the wheel and the Claude Desktop extension (`leis-mcp.mcpb`), publishes the distributions to TestPyPI and then PyPI through trusted publishing, creates the `vX.Y.Z` tag and a GitHub release with the distributions and the extension attached, and finally publishes `server.json` to the [MCP Registry](https://registry.modelcontextprotocol.io/) as `io.github.mazulo/leis-mcp`.

The run form has two checkboxes, both on by default: **Publish to TestPyPI** and **Publish to PyPI**. With only TestPyPI checked the run stops there, with no PyPI upload, tag, release or registry entry, which is how to rehearse a version. With only PyPI checked it skips TestPyPI. The version field is optional and defaults to `pyproject.toml`.

## One-time setup

1. On PyPI and on TestPyPI, add a pending trusted publisher (Account settings → Publishing):
   - Project name: `leis-mcp`
   - Owner: `mazulo`, repository: `leis-mcp`
   - Workflow name: `publish.yml`
   - Environment name: `pypi` on PyPI, `testpypi` on TestPyPI
2. On GitHub, create the `pypi` and `testpypi` environments (Settings → Environments). Restricting their deployment branches to `main` and adding yourself as a required reviewer on `pypi` is recommended.
3. Run the [Index](workflows/index.yml) workflow once (Actions → Index → Run workflow). It creates the `index` prerelease that `leis-mcp index` and first runs download from, and refreshes it every Monday.

No PyPI or registry token is stored in the repository: the MCP Registry accepts the workflow's GitHub OIDC token for the `io.github.mazulo/*` namespace, and checks the PyPI package through the `mcp-name` comment in the README.

## Cutting a release

1. Bump `version` in `pyproject.toml` on `main` and push. The extension manifest and `server.json` take the version from it at build time.
2. Actions → Publish → Run workflow (branch `main`). When the version field is filled it must match `pyproject.toml`.

The workflow refuses to run outside `main`, with neither checkbox selected, when the version does not match `pyproject.toml`, or when the tag already exists.

## The prebuilt index

The `index` prerelease holds `index.json` and the gzip archive it names (`index-<commit>.sqlite.gz`). The archive is uploaded before `index.json`, and older archives are deleted after it, so a client never reads a manifest whose archive is missing. Clients check the sha256 and the commit inside the archive before using it. `leis-mcp index --from-git` still builds the index locally from a blobless clone.
