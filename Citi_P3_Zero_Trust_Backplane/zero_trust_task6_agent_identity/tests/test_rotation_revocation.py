import base64
import json
from datetime import timedelta

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import NOW, load, publish, read_envelope
from zt_task6.registry import RegistryError, RegistryIntegrityError
from zt_task6.revocation import revoke_agent_key
from zt_task6.rotation import rotate_agent_key
from zt_task6.signing import sign_request
from zt_task6.verification import IdentityVerificationError, RequestVerifier


def pub_b64(private):
    return base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")


def key_file(private, path):
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    return path


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def signed(private, tmp_path, at, nonce):
    return sign_request("agent", "tool", key_file(private, tmp_path / f"{nonce}.key"),
                        nonce=nonce, timestamp=stamp(at))


def passes(registry, request, at):
    try:
        RequestVerifier(registry, max_age_seconds=60).verify(request, now=at)
        return True
    except IdentityVerificationError:
        return False


def rotate(env, new_key, *, at, grace=300):
    return rotate_agent_key(env.path, root_private_key_path=env.private, root_key_id="root-v1",
                            agent_identity="agent", new_public_key=new_key,
                            grace_seconds=grace, now=at)


def revoke(env, **kw):
    kw.setdefault("actor", "alice")
    kw.setdefault("reason", "suspected leak")
    kw.setdefault("now", NOW)
    return revoke_agent_key(env.path, root_private_key_path=env.private, root_key_id="root-v1",
                            agent_identity="agent", **kw)


# ---------------- AUTHN-4: rotation ----------------

def test_new_key_verifies_immediately_after_rotation(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    new = Ed25519PrivateKey.generate()
    rotate(env, pub_b64(new), at=NOW)
    registry = load(env)
    assert passes(registry, signed(new, tmp_path, NOW, "n1"), NOW)


def test_old_key_still_verifies_during_grace_period(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    rotate(env, pub_b64(Ed25519PrivateKey.generate()), at=NOW, grace=300)
    registry = load(env)
    during = NOW + timedelta(seconds=299)
    assert passes(registry, signed(env.agent, tmp_path, during, "n2"), during)


def test_old_key_rejected_after_grace_expires(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    rotate(env, pub_b64(Ed25519PrivateKey.generate()), at=NOW, grace=300)
    registry = load(env)
    after = NOW + timedelta(seconds=301)
    assert not passes(registry, signed(env.agent, tmp_path, after, "n3"), after)


def test_rotation_resigns_registry_and_bypass_is_detected(env):
    publish(env, at=NOW - timedelta(days=1))
    before = read_envelope(env)["signature"]
    rotate(env, pub_b64(Ed25519PrivateKey.generate()), at=NOW)
    assert read_envelope(env)["signature"] != before
    load(env)  # root-signed rotation loads fine
    forged = read_envelope(env)
    forged["payload"]["identities"]["agent"][0]["valid_until"] = None  # edit without re-signing
    env.path.write_text(yaml.safe_dump(forged), encoding="utf-8")
    with pytest.raises(RegistryIntegrityError):
        load(env)


def test_rotation_input_validation(env):
    publish(env, at=NOW - timedelta(days=1))
    new = pub_b64(Ed25519PrivateKey.generate())
    with pytest.raises(RegistryError):
        rotate(env, new, at=NOW, grace=0)
    with pytest.raises(RegistryError):
        rotate(env, env.key, at=NOW)  # same key is not a rotation
    with pytest.raises(RegistryError):
        rotate_agent_key(env.path, root_private_key_path=env.private, root_key_id="root-v1",
                         agent_identity="ghost", new_public_key=new, grace_seconds=60, now=NOW)


# ---------------- AUTHN-5: revocation ----------------

def test_revoked_key_rejected_even_though_signature_is_valid(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    registry = load(env)
    request = signed(env.agent, tmp_path, NOW, "r1")
    assert passes(registry, request, NOW)
    revoke(env)
    fresh = load(env)
    assert not passes(fresh, signed(env.agent, tmp_path, NOW + timedelta(seconds=1), "r2"),
                      NOW + timedelta(seconds=1))


def test_revocation_takes_effect_on_reload_with_no_grace(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    registry = load(env)
    revoke(env)
    registry.reload()  # a running verifier observes revocation at its next reload
    later = NOW + timedelta(seconds=1)
    assert not passes(registry, signed(env.agent, tmp_path, later, "r3"), later)


def test_only_key_revoked_means_no_auth_until_new_key_registered(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    revoke(env)
    registry = load(env)
    assert registry.lookup("agent", at=NOW + timedelta(seconds=1)) == ()
    new = Ed25519PrivateKey.generate()
    rotate(env, pub_b64(new), at=NOW + timedelta(seconds=2))
    registry.reload()
    at = NOW + timedelta(seconds=3)
    assert passes(registry, signed(new, tmp_path, at, "r4"), at)
    assert not passes(registry, signed(env.agent, tmp_path, at, "r5"), at)


def test_revocation_writes_attributable_audit_event_once(env, tmp_path):
    publish(env, at=NOW - timedelta(days=1))
    audit = tmp_path / "audit" / "native_mas_audit.jsonl"
    assert revoke(env, audit_log_path=audit) == [env.key_id]
    event = json.loads(audit.read_text().splitlines()[0])
    assert event["event_type"] == "IDENTITY_REVOKED"
    assert (event["agent_identity"], event["key_id"], event["actor"], event["reason"]) == (
        "agent", env.key_id, "alice", "suspected leak")
    assert event["timestamp"].startswith("2026-10-08")
    assert revoke(env, audit_log_path=audit) == []  # idempotent, no duplicate event
    assert len(audit.read_text().splitlines()) == 1


def test_revocation_input_validation(env):
    publish(env, at=NOW - timedelta(days=1))
    with pytest.raises(RegistryError):
        revoke(env, actor=" ")
    with pytest.raises(RegistryError):
        revoke(env, reason="")
    with pytest.raises(RegistryError):
        revoke(env, key_id="0" * 64)
    with pytest.raises(RegistryError):
        revoke_agent_key(env.path, root_private_key_path=env.private, root_key_id="root-v1",
                         agent_identity="ghost", actor="a", reason="r")
