# Local connector development scaffold

This package is an **intermediate, local-only development scaffold**. It can
index plain text from folders selected by the user into a private local SQLite
database, show local status, and explicitly clear its derived index. It does not
connect to the cloud, pair a device, accept server commands, fetch files, or
enable real users.

## Local development

Install this distribution into a development environment:

```sh
python -m pip install -e .
```

Create `ConnectorConfig` with a private database path outside every folder to be
indexed. Construct `ConnectorRuntime`, call `select_roots([...])`, then call
`start()` to launch the development page on an ephemeral `127.0.0.1` port. Open
the returned address in a browser. The page accepts one local folder path per
line. No source file contents are uploaded. `close()` stops the page and watcher.

Folder paths are pinned when selected. If a selected directory disappears or
changes identity, indexing pauses and the user must select it again. Index
deletion is confirmed in the page, removes only derived local content, and
pauses future indexing. Select **Resume indexing** explicitly to continue.
Selecting folders does not implicitly resume after deletion.

The database directory and SQLite file must be private to the current user and
must not be inside a selected folder; such folder selections are refused. Unsafe
permissions, links, unsupported
platform semantics, or an uncertain file identity cause the scaffold to fail
closed. This build currently permits safe file access on POSIX platforms only;
Windows file access is unavailable until the P18-T07 native handle and ACL work
is complete. Local UI requests are loopback-only and protected by host/origin
checks and a per-run CSRF token.

## Not provided by this scaffold

- Native Windows handle and ACL enforcement, three-platform race evidence,
  tray app, installer or production folder picker; owned by P18-T07.
- Device-side signed command protocol, authentication and cloud search; owned
  by P18-T05/T06 and still gated by Q-19.
- mTLS deployment, OS device-key-store integration, or real-user activation.

The session API always raises `ProtocolError` and never calls an injected
transport. That seam exists only so callers fail clearly until the reviewed
device protocol is implemented.
