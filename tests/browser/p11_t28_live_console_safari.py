"""Compatibility entry point for P11-T28's Safari regression walkthrough.

Workspace deletion now goes through the P11-T29 reviewed action. Keep the old
acceptance call shape while exercising the new owner confirmation and retained
audit blocker in Safari.
"""

from __future__ import annotations

from pathlib import Path
from runpy import run_path


def run_safari_console(
    base_url: str,
    admin: str,
    owner: str,
    password: str,
    admin_workspace: int,
    owner_workspace: int,
    audit_workspace: int,
) -> dict:
    """Preserve the P11-T28 call signature; admin denial is asserted by HTTP test."""
    del admin, admin_workspace
    helper = run_path(str(Path(__file__).with_name("p11_t29_offboarding_safari.py")))
    owner_result = helper["run_safari_offboarding"](
        base_url,
        owner,
        password,
        owner_workspace,
        protected=False,
    )
    audit_result = helper["run_safari_offboarding"](
        base_url,
        owner,
        password,
        audit_workspace,
        protected=True,
    )
    return {"owner": owner_result, "retained_audit": audit_result}


if __name__ == "__main__":
    raise SystemExit("This compatibility helper is invoked by the P11-T28 live-console acceptance test")
