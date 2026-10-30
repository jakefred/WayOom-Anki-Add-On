"""Artifact checks and an upgrade simulation, never a real Anki installation."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from scripts.package_addon import ROOT, build_package
from wayoom.credentials import CredentialService
from wayoom.storage import ProfileStore


class FakeVault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="wayoom-package-test-")
        cls.base = Path(cls.temporary.name)
        cls.artifact = cls.base / "wayoom.ankiaddon"
        build_package(ROOT, cls.artifact)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_archive_contains_runtime_and_licenses_without_development_or_user_data(self):
        with ZipFile(self.artifact) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(names, sorted(set(names)))
            for required in ("__init__.py", "manifest.json", "panel.html", "README.md",
                             "wayoom/runtime.py", "wayoom/default_models.json",
                             "wayoom/provider_routes.json", "web/dist/panel.js",
                             "vendor/runtime/common/keyring/__init__.py",
                             "vendor/runtime/common/win32ctypes/__init__.py",
                             "vendor/runtime/common/secretstorage/__init__.py"):
                self.assertIn(required, names)
            for abi in ("cp312", "cp313", "cp314"):
                self.assertTrue(any(n.startswith(f"vendor/runtime/linux/{abi}/_cffi_backend") for n in names))
            self.assertTrue(any(n.endswith("_rust.abi3.so") for n in names))
            forbidden = {"user_files", "tests", "test", "docs", "scripts", "node_modules",
                         "__pycache__", ".git", ".venv", "wheels"}
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                self.assertFalse(path.is_absolute() or ".." in path.parts)
                self.assertFalse(set(path.parts) & forbidden, info.filename)
                self.assertFalse(info.filename.endswith((".pyc", ".whl", ".map")))
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16, 0o100644)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["package"], "wayoom")
            self.assertEqual(manifest["min_point_version"], 260903)
            self.assertEqual(manifest["human_version"], "0.1.0")
            self.assertIn(b"Remove all keys", archive.read("README.md"))
            notices = archive.read("THIRD_PARTY_NOTICES.txt").decode()
            self.assertIn("@ai-sdk/provider-utils 5.0.72", notices)
            self.assertIn("END OF TERMS AND CONDITIONS", notices)
            self.assertIn("zod 4.6.7", notices)
            self.assertTrue(any("keyring-25.7.0.dist-info/licenses/LICENSE" in n for n in names))

    def test_repeated_build_is_byte_identical(self):
        output = self.base / "repeated.ankiaddon"
        digest = build_package(ROOT, output)
        self.assertEqual(self.artifact.read_bytes(), output.read_bytes())
        self.assertEqual(digest, sha256(output.read_bytes()).hexdigest())
        self.assertEqual(output.with_suffix(".ankiaddon.sha256").read_text(),
                         f"{digest}  {output.name}\n")

    def test_dirty_checkout_and_crlf_do_not_enter_archive_and_bad_wheel_preserves_artifact(self):
        # A disposable source tree, with the same pinned dependencies. Never
        # inject sentinel secrets into the shared checkout.
        root = self.base / "dirty-source"
        root.mkdir()
        for name in ("__init__.py", "panel.html", "manifest.json", "README.md", "package.json", "package-lock.json"):
            shutil.copyfile(ROOT / name, root / name)
        for name in ("wayoom", "web/dist", "vendor/wheels", "vendor/licenses"):
            shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "vendor/manifest.json", root / "vendor/manifest.json")
        lock = json.loads((ROOT / "package-lock.json").read_text())
        for name, entry in lock["packages"].items():
            if not name or entry.get("dev"):
                continue
            destination = root / name
            destination.mkdir(parents=True)
            for path in (ROOT / name).iterdir():
                if path.is_file():
                    shutil.copyfile(path, destination / path.name)
        for name in ("user_files/private.json", "vendor/runtime/common/stale.py", "tests/local.py", "scripts/dev.py", "wayoom/local_debug.py"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fake-secret-must-not-ship")
        init = root / "__init__.py"
        init.write_bytes(init.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        output = root / "output.ankiaddon"
        build_package(root, output)
        self.assertEqual(output.read_bytes(), self.artifact.read_bytes())
        wheel = next((root / "vendor/wheels").glob("*.whl"))
        wheel.write_bytes(b"corrupted-wheel")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            build_package(root, output)
        self.assertEqual(output.read_bytes(), self.artifact.read_bytes())

    def test_packaged_credential_dependencies_import_without_site_packages(self):
        installation = self.base / "dependency-import"
        with ZipFile(self.artifact) as archive:
            archive.extractall(installation)
        check = '''
import importlib, pathlib, platform, sys
root = pathlib.Path(sys.argv[1]) / 'vendor/runtime'
paths = [root / 'common']
if sys.platform == 'linux':
    paths += [root / 'linux/common', root / 'linux' / f'cp{sys.version_info.major}{sys.version_info.minor}']
sys.path[:0] = [str(p) for p in paths]
names = ['keyring', 'jaraco.classes', 'jaraco.context', 'jaraco.functools', 'more_itertools']
names += {'win32': ['win32ctypes', 'keyring.backends.Windows'],
          'darwin': ['keyring.backends.macOS'],
          'linux': ['secretstorage', 'jeepney', 'cryptography', 'cffi', '_cffi_backend', 'keyring.backends.SecretService']}[sys.platform]
for name in names:
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).is_relative_to(root), name
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-c", check, str(installation)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_upgrade_preserves_profile_settings_recovery_and_os_credential_references(self):
        installation = self.base / "addons21" / "wayoom"
        with ZipFile(self.artifact) as archive:
            archive.extractall(installation)
        vault = FakeVault()
        service = CredentialService(lambda: vault)
        users = installation / "user_files"
        for profile in ("first", "second"):
            store = ProfileStore(users, profile)
            service.save_key(store, "openai", f"fake-key-{profile}", model_id="saved-model")
            service.select_provider(store, "openai")
            store.update_settings(panelVisible=False, notetypeId=123, ownedNotetypeIds=[123])
            store.prepare({"requestId": f"pending-{profile}", "cardCount": 1,
                           "destination": {"mode": "existing", "deckId": 1}, "providerId": "openai"})
        before = {path.relative_to(users): path.read_bytes() for path in users.rglob("*") if path.is_file()}
        (installation / "obsolete.py").write_text("old release")
        # Model Anki's documented upgrade behavior: replace everything except
        # user_files. This is explicitly not a real Qt AddonManager install.
        for path in installation.iterdir():
            if path.name == "user_files":
                continue
            self.assertTrue(path.resolve().is_relative_to(installation.resolve()))
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        with ZipFile(self.artifact) as archive:
            archive.extractall(installation)
        self.assertFalse((installation / "obsolete.py").exists())
        after = {path.relative_to(users): path.read_bytes() for path in users.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        # Fresh isolated interpreter imports the shipped Python, not the source
        # checkout. Only the external OS-vault interface is doubled.
        check = '''
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from wayoom.credentials import CredentialService
from wayoom.storage import ProfileStore
import wayoom
assert pathlib.Path(wayoom.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
values = {(s, a): v for s, a, v in json.loads(sys.argv[2])}
class Vault:
    def get_password(self, service, account): return values.get((service, account))
service = CredentialService(Vault)
for profile in ('first', 'second'):
    store = ProfileStore(pathlib.Path(sys.argv[1]) / 'user_files', profile)
    summary = service.summary(store)
    assert not summary['setupRequired']
    assert summary['activeProviderId'] == 'openai'
    assert not store.settings()['panelVisible']
    assert store.settings()['providers']['openai']['modelId'] == 'saved-model'
    assert store.job('pending-' + profile)['state'] == 'pending'
'''
        values = json.dumps([[s, a, v] for (s, a), v in vault.values.items()])
        result = subprocess.run([sys.executable, "-I", "-c", check, str(installation), values],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
