"""Opt-in agent identity gate for the MCP server process.

Off unless CITI_ZT_IDENTITY_REGISTRY is set. When on, every tool requires a
``signed_identity`` argument (the JSON signed request produced by
``zt_task6.signing.sign_request``) and this server process verifies it itself,
before the tool body runs. The registry is reloaded on every call, so a
revocation takes effect on the very next request, with no gateway restart.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


class IdentityGate:
    def __init__(self, registry_path, root_public_key_path, root_key_id, *,
                 max_age_seconds=60, audit_log_path=None):
        from zt_task6.registry import PublicKeyRegistry
        from zt_task6.verification import RequestVerifier

        self.registry = PublicKeyRegistry.from_yaml(
            Path(registry_path), root_public_key_path=Path(root_public_key_path),
            expected_root_key_id=root_key_id)
        self.verifier = RequestVerifier(self.registry, max_age_seconds=max_age_seconds)
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None

    @classmethod
    def from_env(cls, env=os.environ):
        registry = env.get("CITI_ZT_IDENTITY_REGISTRY")
        if not registry:
            return None
        root_key = env.get("CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY")
        root_id = env.get("CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID")
        if not root_key or not root_id:
            raise RuntimeError("CITI_ZT_IDENTITY_REGISTRY requires CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY "
                               "and CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID")
        src = env.get("CITI_ZT_TASK6_SRC")
        if src and src not in sys.path:
            sys.path.insert(0, src)
        return cls(registry, root_key, root_id, audit_log_path=env.get("CITI_ZT_GATEWAY_AUDIT"))

    def _audit(self, event_type, tool, caller, reason=None):
        if self.audit_log_path is None:
            return
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({
                "event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(),
                "caller_runtime_id": caller, "target_tool": tool, "reason": reason,
                "enforced_by": "mcp_gateway_process",
            }, sort_keys=True) + "\n")

    def check(self, tool, signed_identity):
        """Return the verified caller id, or raise PermissionError (fail closed)."""
        caller = None
        try:
            if not signed_identity:
                raise ValueError("A signed identity is required")
            request = json.loads(signed_identity)
            if not isinstance(request, dict):
                raise ValueError("Signed identity must be a JSON object")
            caller = request.get("caller_runtime_id")
            if request.get("target_tool") != tool:
                raise ValueError("Signed request was issued for a different tool")
            self.registry.reload()
            self.verifier.verify(request)
        except ValueError as exc:
            self._audit("IDENTITY_VERIFICATION_FAILED", tool, caller, str(exc))
            raise PermissionError(f"IDENTITY_VERIFICATION_FAILED: {exc}") from exc
        self._audit("IDENTITY_VERIFIED", tool, caller)
        return caller

    def wrap(self, fn):
        """Wrap an async tool so it runs only after the identity check passes."""
        tool = fn.__name__
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def gated(*args, signed_identity: str = "", **kwargs):
            self.check(tool, signed_identity)
            return await fn(*args, **kwargs)

        gated.__signature__ = signature.replace(parameters=[
            *signature.parameters.values(),
            inspect.Parameter("signed_identity", inspect.Parameter.KEYWORD_ONLY, default="", annotation=str),
        ])
        return gated
