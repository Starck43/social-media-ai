"""P19-T08: document version history, diff and preview on the chat surface.

Read-only contract: the three tools return typed data and a restore preview
that points at the web decision surface; none of them writes a revision. Each
was added because the endpoint and the CLI already expose it and the parity
table requires the chat to read and preview what the member may see. Refusals
come from the same service checks (ACL scope, boundary) and the registry gate
(`collection.source.view`).
"""

from __future__ import annotations

import pytest

import app.agent.toolset.documents as document_tools

# Real tenant, permission and workspace data: never the legacy test bypass.
pytestmark = pytest.mark.tenancy

TOOL_NAMES = ("documents_history", "documents_diff", "documents_restore_preview")


def test_chat_version_tools_are_registered_read_only():
    from app.agent.tools import get_tool

    for name in TOOL_NAMES:
        spec = get_tool(name)
        assert spec is not None, f"{name} must be registered"
        assert spec.required_permission == "collection.source.view", name
        assert spec.confirm is False, f"{name} is a read and must never be staged as a write"
    assert get_tool("documents_history").handler is document_tools.documents_history


def test_history_and_diff_tool_schemas_match_handlers():
    from app.agent.tools import get_tool

    history = get_tool("documents_history")
    assert set(history.parameters["properties"]) == {"document_id", "limit"}
    assert history.parameters["required"] == ["document_id"]
    diff = get_tool("documents_diff")
    assert set(diff.parameters["properties"]) == {
        "document_id",
        "from_revision_id",
        "to_revision_id",
        "granularity",
    }
    assert diff.parameters["required"] == ["document_id", "from_revision_id", "to_revision_id"]
    preview = get_tool("documents_restore_preview")
    assert set(preview.parameters["properties"]) == {"document_id", "revision_id"}


def test_toolset_module_cannot_reach_the_write_service():
    """A property of the source itself: the module never calls `restore`."""
    import inspect

    source = inspect.getsource(document_tools)
    assert "version_service.restore" not in source
    assert "version_service.list(" in source and "version_service.diff(" in source


def test_approval_link_is_the_web_decision_surface():
    assert document_tools.document_approval_link(7) == "/app/documents/manage/7?boundary=local_only"
