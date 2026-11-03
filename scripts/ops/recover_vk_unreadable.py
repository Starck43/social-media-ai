"""Probe or clear a known-unsent VK refresh marker in a maintenance window.

Uses the configured key; never pass secrets as arguments. No provider calls.
Explicit target and separate owner authorization are required, including dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe or recover one unsent VK refresh marker.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--credential-id", type=int, required=True, help="Expected current credential row")
    parser.add_argument("--target-approved", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Clear the marker after successful verification")
    parser.add_argument("--backup-confirmed", action="store_true")
    args = parser.parse_args(argv)
    if not args.target_approved or not args.writers_stopped:
        parser.error("--target-approved and --writers-stopped are required")
    if args.apply and not args.backup_confirmed:
        parser.error("--apply requires --backup-confirmed")
    if args.user_id <= 0 or args.credential_id <= 0:
        parser.error("IDs must be positive")
    try:
        from app.core.database import async_engine
        from app.services.social.credential_recovery import recover_unreadable_vk

        sync_engine = async_engine.sync_engine
        if sync_engine.echo or sync_engine.logger.isEnabledFor(logging.INFO):
            raise RuntimeError("SQL logging must be disabled")
        result = asyncio.run(
            recover_unreadable_vk(user_id=args.user_id, credential_id=args.credential_id, apply=args.apply)
        )
    except Exception:
        # SQL exceptions may contain connection parameters or bound data.
        print("Recovery did not complete; verify target, key and transaction outcome.", file=sys.stderr)
        return 1
    print("Recovery probe passed; marker unchanged." if result == "eligible" else "Recovery marker cleared.")
    print("Keep writers stopped until the key is verified on every instance; provider renewal remains unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
