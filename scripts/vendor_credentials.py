"""Maintainer-only dependency download. Normal builds/tests never use the network."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
destination = root / "vendor" / "wheels"
destination.mkdir(parents=True, exist_ok=True)
requirements = [line for line in (root / "requirements-credentials.txt").read_text().splitlines()
                if line and not line.startswith("#")]
common = [item for item in requirements if not item.startswith(("cryptography=", "cffi="))]
base = [sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
        "--dest", str(destination)]
subprocess.run([*base, *common], check=True)
for version in ("312", "313", "314"):
    subprocess.run([*base, "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux2014_x86_64",
                    "--implementation", "cp", "--python-version", version,
                    "cffi==2.1.1", *(["cryptography==50.0.2"] if version == "312" else [])], check=True)
manifest = {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted(destination.glob("*.whl"))}
(destination.parent / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
