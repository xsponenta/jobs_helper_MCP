"""Builds the Claude Desktop extension: release/jobs-helper.mcpb

    uv run python scripts/build_mcpb.py

The bundle uses the `uv` server type: Claude Desktop installs Python and the dependencies itself,
so the end user needs nothing but Claude Desktop.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packaging" / "mcpb"
STAGE = ROOT / "dist" / "mcpb" / "jobs-helper"
OUT = ROOT / "release" / "jobs-helper.mcpb"

SERVER_SHIM = '''"""Entry point of the Jobs Helper Claude Desktop extension."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs_helper.mcp_all import main  # noqa: E402

main()
'''


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    shutil.copytree(ROOT / "jobs_helper", STAGE / "jobs_helper",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "agents"))
    deps = ",\n".join(f'    "{d}"' for d in project["dependencies"])
    (STAGE / "pyproject.toml").write_text(
        f'[project]\nname = "jobs-helper-extension"\nversion = "{project["version"]}"\n'
        f'requires-python = "{project["requires-python"]}"\ndependencies = [\n{deps}\n]\n\n[tool.uv]\npackage = false\n')
    (STAGE / "server.py").write_text(SERVER_SHIM)
    manifest = (SRC / "manifest.template.json").read_text().replace("__VERSION__", project["version"])
    json.loads(manifest)  # fail early on a broken template
    (STAGE / "manifest.json").write_text(manifest)
    shutil.copy(SRC / "icon.png", STAGE / "icon.png")
    (STAGE / ".mcpbignore").write_text(".venv/\n__pycache__/\n*.pyc\nuv.lock\n")

    subprocess.run(["npx", "-y", "@anthropic-ai/mcpb", "validate", str(STAGE / "manifest.json")], check=True)
    OUT.parent.mkdir(exist_ok=True)
    subprocess.run(["npx", "-y", "@anthropic-ai/mcpb", "pack", str(STAGE), str(OUT)], check=True)
    print(f"\nBuilt {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
