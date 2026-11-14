# Local connector installation (development builds)

**Not activated or production-certified.** These unsigned PyInstaller bundles
package the local-only scaffold, not a cloud-connected desktop product. Windows
folder access remains denied until native handle/ACL enforcement is delivered.
Three-OS race testing, tray integration, real OS key storage and mTLS deployment
are still required by [P18-T07](tasks/p18-t07.md) and [Q-19](tasks/q-19.md).
Installing a bundle never pairs a device, starts the server runtime or grants
permission to activate users.

## Obtain and check a build

Only use artifacts from the repository's **Local connector unsigned development
bundles** workflow at the reviewed commit. CI follows the existing master-only
Actions policy; opening a dev PR does not produce artifacts. Extract the complete
zip into a new version-specific directory owned by your account. Keep the old
directory for rollback. Do not merge directories, elevate portable builds or
overwrite a running executable. The executable requires no system Python.

`ai-assistant-connector --version` (Windows: `.exe`) probes the packaged version
without opening a database, local page, network channel or indexed folder.

## Windows

Extract the zip into `%LOCALAPPDATA%\Programs\AI Assistant Connector\<version>`.
Launch `ai-assistant-connector.exe --version` from PowerShell. Unsigned pilot
builds can trigger SmartScreen: after checking the repository and reviewed
artifact, use **More info → Run anyway**, where your organization's policy
allows it. Never disable SmartScreen or bypass an administrator's block.

A normal launch currently exits with an unsupported-native-security message;
**Windows indexing is not available**. A successful version probe is only build
evidence, not Windows installation/functional acceptance.

## macOS

Extract into `~/Applications/AI Assistant Connector/<version>`; run the contained
`ai-assistant-connector` executable. For the expected unsigned/unnotarized warning,
review the artifact, attempt to open it, then use **System Settings → Privacy &
Security → Open Anyway** and confirm where permitted. Do not disable Gatekeeper
or remove quarantine recursively. If managed policy blocks execution, stop.

## Linux

Extract into `~/.local/opt/ai-assistant-connector/<version>`. Run
`chmod u+x ai-assistant-connector` if the extraction tool lost executable mode,
then `./ai-assistant-connector`.

The Linux workflow additionally produces x86-64 AppImage and deb artifacts.
These require Ubuntu 24.04-compatible libraries (glibc 2.39 or newer); they are
not certified on older distributions or other architectures. AppImage:
`chmod u+x <artifact>.AppImage`, then `./<artifact>.AppImage`; if FUSE is unavailable,
use `./<artifact>.AppImage --appimage-extract-and-run`. Debian package:
`sudo apt install ./<artifact>.deb` where administrator policy permits. Run
`ai-assistant-connector` from a terminal or its development desktop entry. The
deb contains no maintainer scripts, service or login auto-start. Native Linux
artifact execution remains unrun on the remote macOS lane; CI must prove it.

## Local use and data

On supported POSIX hosts, launch in a terminal. The connector opens its protected
loopback page in your browser; `--no-browser` prints the address instead. Select
folders explicitly on that page. Stop with Ctrl+C before replacing a build.
There is no background service, login auto-start, tray process or inbound cloud
listener. See the [scaffold boundary](../local_connector/README.md).

The default private data directory contains the local SQLite index, approved
root identities and lock/sidecar files:

| OS | Directory |
| --- | --- |
| macOS | `~/Library/Application Support/AI Assistant Connector` |
| Linux | `~/.local/share/ai-assistant-connector` |
| Windows (reserved, runtime denied) | `%LOCALAPPDATA%\AI Assistant Connector` |

Do not select a folder containing the data directory. Local originals remain in
their selected folders; installation/uninstallation never removes originals.
Tesseract and language data are not bundled: optional OCR requires a separately
installed supported Tesseract; a missing executable returns an unavailable result.

## Updates and rollback

Binary signing/notarization is deferred by [D6](audit/q/q-18-owner-decisions.md),
but **update manifests still require an authenticated signature**. No live feed,
signing credentials, production trust anchor or automatic installation is enabled
by this development work. Never treat an unsigned checksum list as authentication.
Do not load a trust key supplied by the same untrusted download as the manifest.

The offline verifier in `local_connector/packaging/updates.py` authenticates an
Ed25519 detached signature (64 raw bytes) over the **exact UTF-8 manifest bytes**
using an authenticated release-bundled public key (32 raw bytes). Verification
precedes JSON parsing. The closed manifest schema is:

```json
{
  "schema": 1,
  "product": "ai-assistant-connector",
  "version": "0.3.0",
  "platform": "linux-x86_64",
  "sha256": "<64 lowercase hexadecimal characters>",
  "size": 123456,
  "issued_at": 1800000000,
  "expires_at": 1800086400
}
```

Times are integer Unix seconds with at most a 30-day validity period; the
release must be newer than the installed version. Unknown/duplicate fields,
wrong product/platform, invalid signatures, expired/future manifests, malformed
versions, and artifact size/hash mismatches are rejected. This module provides
verification, **not an automatic installer**: it has no network, staging,
installation, extraction, execution or rollback authority. Its `cryptography`
dependency belongs to packaging/test tools and the frozen verification command,
not the unchanged runtime wheel.

Release preparation (authorized release operator only):

```sh
python local_connector/packaging/sign_manifest.py --artifact <bundle> \
  --version <version> --platform <platform> --key <private-ed25519.pem> \
  --encrypted-key --output <manifest.json>
python local_connector/packaging/build.py --output <build-directory> \
  --update-public-key <authenticated-32-byte-public-key>
```

The operator supplies an existing offline key; these commands neither create
keys nor publish releases. Keep private keys outside the repository, restricted
to the release account; never pass passwords in command arguments or CI logs.
The build embeds the authenticated public key. The end user cannot supply a
replacement trust key through CLI, environment, working directory or manifest.
Default development builds have an empty trust anchor and deny all updates.
Key rotation/revocation must be reviewed before enabling a feed; no production
key or signing secret is provisioned by this PR.

The frozen executable supports:

```sh
ai-assistant-connector --verify-update <manifest.json> <manifest.json.sig> <bundle>
```

Success authenticates bounded artifact bytes at verification time only; it does
not apply them. A future installer must reverify at use, maintain durable update
state and preserve private-directory/no-follow semantics. Never treat this
verification receipt as authorization to execute a later modified artifact.

Until the remaining updater/OS acceptance gates pass, updates are manual: stop,
verify the reviewed artifact and use a fresh version directory. Keep the previous
build and a private offline backup of local derived data before upgrading. To
roll back, stop the new build and run the previously verified build; never run
old and new processes against the same index concurrently. Schema downgrade is
not promised; restore its matching backup or delete/rebuild derived data, never
copy old state over revocation/deletion fences once pairing is implemented.

## Uninstall and complete removal

1. Stop the terminal process; verify no connector process is running.
2. Optionally use the page's confirmed **Delete index** before stopping: it pauses
   indexing and deletes derived content only, not the installation directory.
3. Remove each version-specific extracted installation directory and downloaded
   archive. Do not remove their parent directory if it contains unrelated apps.
   For deb installs use `sudo apt remove ai-assistant-connector`; this removes
   packaged application files but intentionally preserves personal derived data.
   For AppImage remove the downloaded file and any extraction/shortcut you made.
4. To remove local state completely, remove only the exact per-OS data directory
   above, including SQLite sidecars/lock files. Source folders are untouched.
5. Remove any shortcuts you created and any explicit private index backups. OS
   snapshots/backups and physical media erasure require separate administration;
   deleting a directory is not a guarantee of forensic erasure.

This scaffold holds no pairing credential or OS key-store entry. A later paired
build also requires server-side device revocation and explicit OS credential
cleanup; deleting this local directory alone would not revoke a device.
