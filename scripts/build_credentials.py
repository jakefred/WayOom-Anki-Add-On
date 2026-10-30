"""Verify pinned wheel hashes and extract runtime dependencies without pip/network."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
vendor = root / "vendor"
manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
for name, digest in manifest.items():
    wheel = vendor / "wheels" / name
    if sha256(wheel.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Credential dependency checksum mismatch: {name}")
    if name.startswith("cffi-"):
        target = vendor / "runtime" / "linux" / name.split("-")[2]
    elif name.startswith("cryptography-"):
        target = vendor / "runtime" / "linux" / "common"
    else:
        target = vendor / "runtime" / "common"
    with ZipFile(wheel) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe dependency archive path")
            if member.is_dir() or any(part in {"tests", "test", "docs", "__pycache__"} for part in path.parts):
                continue
            output = target.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(member))
print(f"Built credential runtime from {len(manifest)} verified wheels")
