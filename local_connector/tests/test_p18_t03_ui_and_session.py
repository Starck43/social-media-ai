from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from local_connector.session import OutboundSessionClient, ProtocolError
from local_connector.ui import LocalStatusServer


def _request(url: str, *, method: str = "GET", body: bytes | None = None, token: str | None = None):
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("X-CSRF-Token", token)
    return urllib.request.urlopen(request, timeout=2)


def test_loopback_ui_requires_csrf_for_folder_changes(tmp_path: Path) -> None:
    server = LocalStatusServer()
    host, port = server.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            _request(f"http://{host}:{port}/folders", method="POST", body=b'{"roots":[]}')
        assert error.value.code == 403
        body = json.dumps({"roots": [str(tmp_path)]}).encode()
        response = _request(f"http://{host}:{port}/folders", method="POST", body=body, token=server.csrf_token)
        assert response.status == 200
        status = json.loads(_request(f"http://{host}:{port}/status").read())
        assert status["roots"] == [str(tmp_path.resolve())]
        assert host == "127.0.0.1"
        assert port != 0
    finally:
        server.close()
        thread.join(timeout=2)


def test_outbound_session_rejects_paths_and_binds_response() -> None:
    sent: list[dict] = []

    def transport(envelope: dict) -> dict:
        sent.append(envelope)
        return {
            "request_id": envelope["request_id"],
            "request_digest": envelope["request_digest"],
            "outcome": "ok",
        }

    client = OutboundSessionClient(
        transport, device_id="d", tenant_id="t", owner_id="o", actor_id="a", signer=lambda _: "sig"
    )
    response = client.request("health")
    assert response["outcome"] == "ok"
    assert sent[0]["signature"] == "sig"
    assert sent[0]["operation"] == "health"

    with pytest.raises(ProtocolError):
        client.request("file.metadata", {"path": "/private/file.txt"})
    with pytest.raises(ProtocolError):
        client.request("unknown")
