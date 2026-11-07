from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from local_connector.session import OutboundSessionClient, ProtocolError
from local_connector.ui import FolderSelection, LocalStatusServer


def _request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("X-CSRF-Token", token)
    return urllib.request.urlopen(request, timeout=2)


def _csrf(page: str) -> str:
    match = re.search(r'const csrf="([A-Za-z0-9_-]+)"', page)
    assert match is not None
    return match.group(1)


def test_loopback_ui_serves_development_page_and_requires_csrf(tmp_path: Path) -> None:
    selection = FolderSelection()
    server = LocalStatusServer(selection)
    host, port = server.start()
    try:
        base = f"http://{host}:{port}"
        response = _request(base + "/")
        page = response.read().decode()
        assert "Local connector" in page
        assert "Folder paths on this device" in page
        assert response.headers["Cache-Control"].startswith("no-store")
        assert "script-src 'nonce-" in response.headers["Content-Security-Policy"]

        body = json.dumps({"roots": [str(tmp_path)]}).encode()
        with pytest.raises(urllib.error.HTTPError) as error:
            _request(base + "/folders", method="POST", body=body)
        assert error.value.code == 403

        response = _request(base + "/folders", method="POST", body=body, token=_csrf(page))
        assert response.status == 200
        status = json.loads(_request(base + "/status").read())
        assert status["roots"] == [str(tmp_path.resolve())]
        assert host == "127.0.0.1"
        assert port != 0
    finally:
        server.close()


def test_ui_rejects_dns_rebinding_host_and_foreign_origin(tmp_path: Path) -> None:
    server = LocalStatusServer(FolderSelection())
    host, port = server.start()
    try:
        base = f"http://{host}:{port}"
        with pytest.raises(urllib.error.HTTPError) as wrong_host:
            _request(base + "/status", headers={"Host": f"attacker.test:{port}"})
        assert wrong_host.value.code == 403

        page = _request(base + "/").read().decode()
        with pytest.raises(urllib.error.HTTPError) as wrong_origin:
            _request(
                base + "/folders",
                method="POST",
                body=json.dumps({"roots": [str(tmp_path)]}).encode(),
                token=_csrf(page),
                headers={"Origin": f"http://attacker.test:{port}"},
            )
        assert wrong_origin.value.code == 403
    finally:
        server.close()


def test_local_session_seam_never_calls_injected_transport() -> None:
    sent: list[dict] = []

    def transport(envelope: dict) -> dict:
        sent.append(envelope)
        return {}

    client = OutboundSessionClient(transport, signer=lambda _: "sig")
    with pytest.raises(ProtocolError, match="disabled"):
        client.request("health")
    with pytest.raises(ProtocolError, match="disabled"):
        client.request("file.fetch", {"object_id": "file-1"})
    assert sent == []
