"""Build a deterministic, allowlisted .ankiaddon; never read user_files."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.build_credentials import runtime_files

MODULES = ("__init__", "bridge", "cards", "credentials", "jobs", "models",
           "operations", "proxy", "runtime", "settings", "settings_dialog", "storage", "validation")


def text_bytes(path):
    # Git's Windows CRLF checkout must not change the release digest.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")


def js_notices(root):
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    notices = []
    for name, entry in sorted(lock["packages"].items()):
        if not name or entry.get("dev"):
            continue
        directory = root / name
        metadata = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        if metadata["version"] != entry["version"]:
            raise ValueError("Installed JS dependencies differ from package-lock.json; run npm ci")
        licenses = sorted(path for path in directory.iterdir()
                          if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")))
        # This pinned Vercel package omits LICENSE from its npm tarball. Retain
        # the same project's attribution, plus the full Apache text below.
        if name == "node_modules/@ai-sdk/provider-utils" and metadata.get("license") == "Apache-2.0":
            licenses = [root / "node_modules/ai/LICENSE"]
        if not licenses:
            raise ValueError(f"Missing dependency license: {name}")
        notices.append(f"{metadata['name']} {entry['version']}\n")
        for path in licenses:
            notices.append(f"{path.name}\n{text_bytes(path).decode('utf-8')}\n")
    notices.append(text_bytes(root / "vendor/licenses/Apache-2.0.txt").decode("utf-8"))
    return "\n".join(notices).encode("utf-8")


def package_files(root):
    # Never walk the checkout or vendor/runtime: they may contain local state,
    # stale dependencies, secrets, caches, or development files.
    for name in ("__init__.py", "panel.html", "manifest.json", "README.md"):
        yield name, text_bytes(root / name)
    for name in (*[f"{module}.py" for module in MODULES], "default_models.json", "provider_routes.json"):
        yield f"wayoom/{name}", text_bytes(root / "wayoom" / name)
    yield "web/dist/panel.js", text_bytes(root / "web/dist/panel.js")
    yield "THIRD_PARTY_NOTICES.txt", js_notices(root)
    yield "vendor/manifest.json", text_bytes(root / "vendor/manifest.json")
    for name, content in runtime_files(root / "vendor"):
        yield f"vendor/runtime/{name}", content


def build_package(root, output):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    if manifest["human_version"] != version:
        raise ValueError("Manifest and package.json versions differ")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A failed checksum/build cannot replace a previously successful artifact.
    with tempfile.TemporaryDirectory(prefix="wayoom-package-", dir=output.parent) as temporary:
        candidate = Path(temporary) / "addon.zip"
        with ZipFile(candidate, "w", compression=ZIP_STORED) as archive:
            seen = set()
            for name, content in sorted(package_files(root)):
                if name in seen:
                    raise ValueError(f"Duplicate archive path: {name}")
                seen.add(name)
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        candidate.replace(output)
    digest = sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8", newline="\n")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    subprocess.run(["node", "scripts/build.mjs"], cwd=ROOT, check=True)
    version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["human_version"]
    output = args.output or ROOT / "dist" / f"wayoom-{version}.ankiaddon"
    digest = build_package(ROOT, output)
    print(f"{output}\nSHA-256: {digest}")


if __name__ == "__main__":
    main()
