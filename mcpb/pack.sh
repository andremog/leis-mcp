#!/usr/bin/env bash
# Builds leis-mcp.mcpb, the one-click Claude Desktop extension. With server.type "uv" the host installs
# Python and the dependencies from pyproject.toml/uv.lock, so the bundle carries only the source.
# Usage: mcpb/pack.sh [output directory, default dist-mcpb]
set -euo pipefail

MCPB=(npx --yes @anthropic-ai/mcpb@2.1.2)
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$root/dist-mcpb}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

# The manifest takes its version from pyproject.toml, so a release bumps one file.
version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/pyproject.toml" | head -1)"
python3 - "$root/mcpb/manifest.json" "$stage/manifest.json" "$version" <<'EOF'
import json, sys

source, target, version = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    manifest = json.load(handle)
manifest["version"] = version
with open(target, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, ensure_ascii=False, indent=2)
EOF

cp "$root/mcpb/icon.png" "$root/mcpb/.mcpbignore" "$root/pyproject.toml" "$root/uv.lock" \
  "$root/README.md" "$root/LICENSE" "$stage/"
cp -R "$root/src" "$stage/src"

"${MCPB[@]}" validate "$stage/manifest.json"
mkdir -p "$out"
"${MCPB[@]}" pack "$stage" "$out/leis-mcp.mcpb"
