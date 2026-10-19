"""AUTHN-5: immediate, root-signed revocation of an agent key."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .registry import RegistryError, update_signed_registry


def revoke_agent_key(
    registry_path: Path,
    *,
    root_private_key_path: Path,
    root_key_id: str,
    agent_identity: str,
    actor: str,
    reason: str,
    key_id: str | None = None,
    audit_log_path: Path | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Mark keys revoked and re-sign the registry; returns the key IDs newly revoked.

    With no ``key_id`` every active key of the identity is revoked. There is no grace
    period: lookup excludes a revoked key as soon as a verifier reloads the registry, and
    a fresh process never sees it. An identity left with no active key cannot
    authenticate until a new key is registered (see rotation). Revoking an already
    revoked key is a no-op and writes no audit event.
    """
    if not actor or not actor.strip():
        raise RegistryError("Revocation requires the actor performing it")
    if not reason or not reason.strip():
        raise RegistryError("Revocation requires a reason")
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    revoked: list[str] = []

    def mutate(payload: dict) -> bool:
        entries = payload["identities"].get(agent_identity)
        if entries is None:
            raise RegistryError("Unknown agent identity")
        targets = [e for e in entries if key_id is None or e["key_id"] == key_id]
        if not targets:
            raise RegistryError("Unknown key ID for agent identity")
        for entry in targets:
            if entry["status"] == "active":
                entry["status"] = "revoked"
                revoked.append(entry["key_id"])
        return bool(revoked)

    update_signed_registry(registry_path, root_private_key_path=root_private_key_path,
                           root_key_id=root_key_id, mutate=mutate)
    if revoked and audit_log_path is not None:
        audit_log_path = Path(audit_log_path)
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_log_path.open("a", encoding="utf-8") as log:
            for revoked_key in revoked:
                log.write(json.dumps({
                    "event_type": "IDENTITY_REVOKED",
                    "timestamp": when.isoformat(),
                    "agent_identity": agent_identity,
                    "key_id": revoked_key,
                    "actor": actor,
                    "reason": reason,
                }, sort_keys=True) + "\n")
    return revoked
