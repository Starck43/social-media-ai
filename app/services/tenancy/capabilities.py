"""Composition point for subsystem-owned capability registries.

Future subsystems add their own declarations here; the decision engine stays generic.
"""

from app.services.collection.entitlements import CAPABILITIES as CONNECTOR_CAPABILITIES

CAPABILITIES = CONNECTOR_CAPABILITIES
