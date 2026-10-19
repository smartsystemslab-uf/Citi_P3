"""AUTHN-4: root-signed key rotation with an overlap (grace) period."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .registry import RegistryError, _decode, _parse_time, update_signed_registry


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def rotate_agent_key(
    registry_path: Path,
    *,
    root_private_key_path: Path,
    root_key_id: str,
    agent_identity: str,
    new_public_key: str,
    grace_seconds: int,
    now: datetime | None = None,
) -> str:
    """Register ``new_public_key`` now and retire the identity's active keys after a grace period.

    Requests signed by the old key keep verifying until ``now + grace_seconds`` so in-flight
    agents are not cut off; after that the old key is rejected. The registry is re-signed by
    the root, so a rotation that bypasses this function is detected as tampering. Also the
    recovery path for an identity whose only key was revoked (it has nothing to retire).
    Returns the new key ID.
    """
    if type(grace_seconds) is not int or grace_seconds <= 0:
        raise RegistryError("Grace period must be a positive number of seconds")
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    retire = when + timedelta(seconds=grace_seconds)
    retire_at = _stamp(retire)
    new_key_id = hashlib.sha256(_decode(new_public_key, 32)).hexdigest()

    def mutate(payload: dict) -> bool:
        entries = payload["identities"].get(agent_identity)
        if entries is None:
            raise RegistryError("Unknown agent identity; register it with bootstrap first")
        if any(e["key_id"] == new_key_id for e in entries):
            raise RegistryError("Key is already registered for this identity")
        for entry in entries:
            if entry["status"] == "active" and (
                entry["valid_until"] is None or _parse_time(entry["valid_until"]) > retire
            ):
                entry["valid_until"] = retire_at
        entries.append({"key_id": new_key_id, "public_key": new_public_key, "status": "active",
                        "valid_from": _stamp(when), "valid_until": None})
        return True

    update_signed_registry(registry_path, root_private_key_path=root_private_key_path,
                           root_key_id=root_key_id, mutate=mutate)
    return new_key_id
