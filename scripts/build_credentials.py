"""Verify pinned wheels and extract runtime dependencies without pip/network."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def runtime_files(vendor):
    """Yield pinned wheel contents, independent of any old runtime build."""
    manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in sorted(manifest.items()):
        wheel = vendor / "wheels" / name
        if sha256(wheel.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Credential dependency checksum mismatch: {name}")
        if name.startswith("cffi-"):
            target = PurePosixPath("linux", name.split("-")[2])
        elif name.startswith("cryptography-"):
            target = PurePosixPath("linux", "common")
        else:
            target = PurePosixPath("common")
        with ZipFile(wheel) as archive:
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise ValueError("Unsafe dependency archive path")
                if member.is_dir() or any(part in {"tests", "test", "docs", "__pycache__"} for part in path.parts):
                    continue
                yield (target / path).as_posix(), archive.read(member)


def main():
    vendor = ROOT / "vendor"
    for name, content in runtime_files(vendor):
        output = vendor / "runtime" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    print("Built credential runtime from verified wheels")


if __name__ == "__main__":
    main()
