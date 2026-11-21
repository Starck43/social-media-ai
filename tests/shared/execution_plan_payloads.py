"""Execution-plan payloads used by service and transport tests."""


def mail_plan(connection_id, pin):
    return {
        "version": 1,
        "steps": [
            {"name": "read", "operation": "mail.read_headers", "bindings": ["inbox"], "max_items": 100},
            {"name": "group", "operation": "mail.group_by_sender", "inputs": {"mail": ["read"]}},
            {"name": "save", "operation": "messages.store_groups", "inputs": {"groups": ["group"]}},
        ],
        "bindings": [
            {
                "name": "inbox",
                "capability": "mailru_mail.headers.read",
                "resource_id": connection_id,
                "path": "INBOX",
                "workspace_account": pin,
            }
        ],
    }
