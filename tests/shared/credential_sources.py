"""Load pure credential/egress sources without the eager AI package bootstrap."""

from pathlib import Path

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
outbound = load_isolated_source("_credential_outbound", ROOT / "app/core/outbound.py")
egress = load_isolated_source(
    "_credential_egress", ROOT / "app/services/ai/provider_egress.py", {"app.core.outbound": outbound}
)
boundary = load_isolated_source("_credential_boundary", ROOT / "app/services/ai/credential_boundary.py")
