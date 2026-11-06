"""The wheel that would be installed must carry the parliamentary bundle."""

import subprocess
import zipfile
from pathlib import Path

REQUIRED = (
    "leis_mcp/data/parliamentary/inventory.json",
    "leis_mcp/data/parliamentary/bundle/manifest.json",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/congresso-nacional/resolucoes/2002/resolucao-1-2002.md",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/congresso-nacional/resolucoes/1970/resolucao-1-1970.md",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/congresso-nacional/resolucoes/2006/resolucao-1-2006.md",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/camara-dos-deputados/resolucoes/1989/resolucao-17-1989.md",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/camara-dos-deputados/resolucoes/2001/resolucao-25-2001.md",
    "leis_mcp/data/parliamentary/bundle/norms/parlamentar/federal/senado-federal/resolucoes/1993/resolucao-20-1993.md",
    "leis_mcp/data/parliamentary/bundle/provenance/br_federal_congresso-nacional_resolucao_1_2002.json",
)


def test_wheel_contains_manifest_norms_provenance_and_inventory(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "-o", str(tmp_path)], check=True, cwd=Path(__file__).parents[1])
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    names = set(zipfile.ZipFile(wheels[0]).namelist())
    missing = [path for path in REQUIRED if path not in names]
    assert missing == []
    provenance = [
        name
        for name in names
        if name.startswith("leis_mcp/data/parliamentary/bundle/provenance/") and name.endswith(".json")
    ]
    assert len(provenance) == 6
