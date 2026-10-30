# Credential dependencies

`wheels/` contains the exact distributions pinned in `requirements-credentials.txt`.
`manifest.json` records their SHA-256 hashes. Licenses and distribution metadata
are retained when extracting. These artifacts contain no user credentials.

Run `python scripts/build_credentials.py` to verify and extract them offline into
the ignored `vendor/runtime/` directory. Source checkouts need this build before
OS-protected storage is available; missing imports fail safely to an explicit
session-only choice. Release packaging must include the extracted runtime.

Windows uses keyring's WinVault backend with pywin32-ctypes. macOS uses its native
Keychain backend. Linux uses Secret Service, SecretStorage, jeepney, cryptography,
cffi, and pycparser. jaraco.classes, jaraco.context, jaraco.functools, and
more-itertools are keyring's shared dependencies. No configurable third-party
keyring backend is selected.

The native Linux wheels target x86-64 with glibc 2.28+ and CPython 3.12–3.14.
Other Linux architectures/Python ABIs currently offer session-only storage.
Windows and macOS backends use ctypes and do not need these Linux binaries.
Actual credential stores on all three systems remain manual release gates.

Only maintainers updating the dependency pins should run
`python scripts/vendor_credentials.py`; that command downloads wheels and rewrites
the manifest. Review dependency changes before release (SEC-10).

Backend API reference: https://keyring.readthedocs.io/en/latest/

Release packaging reads the verified wheel contents directly, so untracked or
stale files in `runtime/` are never shipped. The package retains wheel license
files and distribution metadata, while excluding tests, docs, and bytecode.

`licenses/Apache-2.0.txt` is the full Apache 2.0 license text, copied unchanged
from the pinned cryptography wheel's `licenses/LICENSE.APACHE` (also published at
https://www.apache.org/licenses/LICENSE-2.0.txt). It accompanies the bundled JS
notices as several SDK license files contain only an attribution and license link.
The pinned `@ai-sdk/provider-utils` npm tarball omits LICENSE despite declaring
Apache-2.0; packaging includes the same Vercel project's attribution from
`ai/LICENSE` and the full license text. No dependency pins changed in milestone 6.
