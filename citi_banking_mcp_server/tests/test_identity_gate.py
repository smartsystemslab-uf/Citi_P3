import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SRC = Path(__file__).resolve().parents[2] / "Citi_P3_Zero_Trust_Backplane" / "zero_trust_task6_agent_identity" / "src"
sys.path.insert(0, str(SRC))

import demo_gateway_identity
from identity_gate import IdentityGate
from zt_task6.keygen import generate_keypair, write_private_key
from zt_task6.registry import write_signed_registry
from zt_task6.revocation import revoke_agent_key
from zt_task6.signing import sign_request

AGENT, ROOT_ID = "agent_a", "root-test"


@pytest.fixture
def gate_env(tmp_path):
    root = Ed25519PrivateKey.generate()
    private, public = tmp_path / "root.pem", tmp_path / "root-pub.pem"
    private.write_bytes(root.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    public.write_bytes(root.public_key().public_bytes(serialization.Encoding.PEM,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo))
    priv_b64, pub_b64 = generate_keypair()
    key_path = write_private_key("good", priv_b64, tmp_path / "keys")
    registry = tmp_path / "registry.yaml"
    write_signed_registry({AGENT: pub_b64}, registry, root_private_key_path=private, root_key_id=ROOT_ID,
                          valid_from=datetime.now(timezone.utc) - timedelta(minutes=1))
    audit = tmp_path / "audit.jsonl"
    gate = IdentityGate(registry, public, ROOT_ID, audit_log_path=audit)
    return gate, key_path, registry, private, audit, tmp_path


def signed(key_path, tool="t"):
    return json.dumps(sign_request(AGENT, tool, key_path))


def test_valid_identity_passes_and_is_audited(gate_env):
    gate, key, *_, audit, _ = gate_env
    assert gate.check("t", signed(key)) == AGENT
    assert json.loads(audit.read_text())["event_type"] == "IDENTITY_VERIFIED"


@pytest.mark.parametrize("bad", ["", "not json", "[]", "{}"])
def test_missing_or_malformed_identity_is_denied(gate_env, bad):
    gate = gate_env[0]
    with pytest.raises(PermissionError, match="IDENTITY_VERIFICATION_FAILED"):
        gate.check("t", bad)


def test_wrong_key_wrong_tool_and_replay_are_denied(gate_env):
    gate, key, *_ , tmp = gate_env
    other = write_private_key("evil", generate_keypair()[0], tmp / "keys")
    with pytest.raises(PermissionError):
        gate.check("t", signed(other))
    with pytest.raises(PermissionError, match="different tool"):
        gate.check("other_tool", signed(key, "t"))
    request = signed(key)
    gate.check("t", request)
    with pytest.raises(PermissionError, match="already been used"):
        gate.check("t", request)


def test_revocation_applies_on_next_call_without_restart(gate_env):
    gate, key, registry, root_private, audit, _ = gate_env
    gate.check("t", signed(key))
    revoke_agent_key(registry, root_private_key_path=root_private, root_key_id=ROOT_ID,
                     agent_identity=AGENT, actor="op", reason="test")
    with pytest.raises(PermissionError):
        gate.check("t", signed(key))
    assert "IDENTITY_VERIFICATION_FAILED" in audit.read_text()


def test_tampered_registry_fails_closed(gate_env):
    gate, key, registry, *_ = gate_env
    registry.write_text(registry.read_text().replace("active", "revoked") + "\n# edited", encoding="utf-8")
    with pytest.raises(PermissionError):
        gate.check("t", signed(key))


def test_wrap_adds_signed_identity_and_runs_tool_only_after_check(gate_env):
    gate, key, *_ = gate_env
    calls = []

    async def t(x: int) -> str:
        calls.append(x)
        return "ran"

    wrapped = gate.wrap(t)
    assert "signed_identity" in inspect.signature(wrapped).parameters
    assert inspect.signature(wrapped).parameters["x"].annotation is int

    import asyncio
    with pytest.raises(PermissionError):
        asyncio.run(wrapped(1, signed_identity=""))
    assert calls == []
    assert asyncio.run(wrapped(2, signed_identity=signed(key))) == "ran"
    assert calls == [2]


def test_from_env_off_partial_and_on(gate_env, tmp_path):
    gate, _, registry, *_ = gate_env
    assert IdentityGate.from_env({}) is None
    with pytest.raises(RuntimeError):
        IdentityGate.from_env({"CITI_ZT_IDENTITY_REGISTRY": str(registry)})
    on = IdentityGate.from_env({
        "CITI_ZT_IDENTITY_REGISTRY": str(registry),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY": str(tmp_path / "root-pub.pem"),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID": ROOT_ID,
        "CITI_ZT_TASK6_SRC": str(SRC),
    })
    assert isinstance(on, IdentityGate)


@pytest.mark.integration
async def test_real_separate_mcp_process_enforces_identity(tmp_path):
    rows = await demo_gateway_identity.run(tmp_path)
    scenarios = rows[:-1]
    assert len(scenarios) == 7
    assert all(row["ok"] for row in scenarios), [r for r in scenarios if not r["ok"]]
    assert [r["allowed"] for r in scenarios] == [True, False, False, False, False, False, True]
