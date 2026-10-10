"""Real AUTHN-3 crypto across the ADK caller and native tool boundary.

Only the ADK surface and downstream policy decision are replaced: request
signing, the pinned root registry, and the engine's identity gate are real.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from native_mas_runtime.engine import NativeAuthorization, NativeMasBackplane
from zt_task6.registry import PublicKeyRegistry, write_signed_registry
from zt_task6.signing import load_private_key
from zt_task6.verification import RequestVerifier

from test_native_zero_trust_plugin import _call_tool, _context_for, native_plugin


@pytest.fixture()
def identity_integration(native_plugin, tmp_path, monkeypatch):
    plugin, _, _ = native_plugin
    identity = "transaction_processing_agent"
    private_key = load_private_key(plugin._agent_key_paths[identity])
    root = Ed25519PrivateKey.generate()
    root_private_path = tmp_path / "registry-root-private.pem"
    root_private_path.write_bytes(root.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    plugin.config.identity_registry_root_public_key_path.write_bytes(
        root.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    write_signed_registry(
        {identity: base64.b64encode(private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )).decode("ascii")},
        plugin.config.identity_registry_path,
        root_private_key_path=root_private_path,
        root_key_id=plugin.config.identity_registry_root_key_id,
        valid_from=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    registry = PublicKeyRegistry.from_yaml(
        plugin.config.identity_registry_path,
        root_public_key_path=plugin.config.identity_registry_root_public_key_path,
        expected_root_key_id=plugin.config.identity_registry_root_key_id,
    )

    # Bypass bootstrap, which scans repositories and starts the PDP. Exercise
    # the production begin_tool/should_execute methods with real authentication.
    engine = NativeMasBackplane.__new__(NativeMasBackplane)
    engine.mode = "enforce"
    engine.shadow_allow_critical = True
    engine.identity_verifier = RequestVerifier(registry)
    engine.audit_path = tmp_path / "identity-integration-audit.jsonl"
    engine._audit_seq = 0
    placement = {"placement_id": "pep.test.tool", "critical": False}
    engine.find_tool_placement = Mock(return_value=placement)

    order = []
    real_verify = engine.identity_verifier.verify

    def verify(request):
        real_verify(request)
        order.append("verified")

    monkeypatch.setattr(engine.identity_verifier, "verify", verify)

    def authorize(placement, **kwargs):
        order.append("authorize")
        return NativeAuthorization(
            allowed=True, would_block=False, mode=engine.mode,
            reason="TEST_POLICY_ALLOW", source="TEST_PDP", placement=placement,
            request=None, runtime_authorization=None, authorization_latency_ms=0.0,
            decision="ALLOW",
        )

    engine.authorize = Mock(side_effect=authorize)
    pending = []
    real_begin = engine.begin_tool

    def begin(*args, **kwargs):
        result = real_begin(*args, **kwargs)
        pending.append(result)
        return result

    monkeypatch.setattr(engine, "begin_tool", begin)
    plugin.engine = engine
    return SimpleNamespace(
        plugin=plugin, engine=engine, order=order, pending=pending,
        key_path=plugin._agent_key_paths[identity],
    )


def test_plugin_signed_identity_passes_real_verifier_before_policy(identity_integration):
    case = identity_integration
    args = {"transaction_id": "TXN-1001", "caller_runtime_id": "untrusted-assertion"}
    assert _call_tool(case.plugin, _context_for(case.plugin), args) is None
    assert case.order == ["verified", "authorize"]
    case.engine.authorize.assert_called_once()
    policy_args = case.engine.authorize.call_args.kwargs
    assert policy_args["caller_runtime_id"] == "transaction_processing_agent"
    assert policy_args["target_runtime_id"] == "get_transaction_context"
    assert policy_args["parameters"] == args
    assert case.pending[0].authorization.allowed


@pytest.mark.parametrize("mode", ["enforce", "shadow"])
@pytest.mark.parametrize("failure", ["wrong_mapped_key", "tampered_signature"])
def test_real_identity_failure_blocks_policy_and_execution(
    identity_integration, monkeypatch, mode, failure,
):
    case = identity_integration
    case.engine.mode = mode
    if failure == "wrong_mapped_key":
        # A valid private key is insufficient unless the root-signed registry
        # binds its public key to this exact runtime agent identity.
        other_key = Ed25519PrivateKey.generate()
        case.key_path.write_text(base64.b64encode(other_key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )).decode("ascii"), encoding="utf-8")
    else:
        sign = case.plugin._sign_request

        def tampered_sign(**kwargs):
            request = sign(**kwargs)
            signature = bytearray(base64.b64decode(request["signature"]))
            signature[0] ^= 1
            request["signature"] = base64.b64encode(signature).decode("ascii")
            return request

        monkeypatch.setattr(case.plugin, "_sign_request", tampered_sign)

    blocked = _call_tool(case.plugin, _context_for(case.plugin))
    assert blocked["status"] == "zero_trust_denied"
    assert blocked["reason"] == "IDENTITY_VERIFICATION_FAILED"
    case.engine.authorize.assert_not_called()
    assert case.order == []
    assert not case.plugin._states["inv-1"].pending_tools
    # The engine itself must deny even with permissive shadow settings; the
    # caller's additional identity-denial guard cannot hide a regression here.
    auth = case.pending[0].authorization
    assert not case.engine.should_execute(auth, critical=False)
    assert not case.engine.should_execute(auth, critical=True)
    events = [json.loads(line) for line in case.engine.audit_path.read_text(
        encoding="utf-8",
    ).splitlines()]
    assert events[-1]["event_type"] == "IDENTITY_VERIFICATION_FAILED"
