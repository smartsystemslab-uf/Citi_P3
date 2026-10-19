"""Proof that identity is verified inside a SEPARATE MCP server process.

Launches mcp_server.py as a child process (stdio), with the identity gate enabled, and calls the
existing front-office tool get_transaction over MCP. No LLM involved. Run:

    python demo_gateway_identity.py

The agents' normal CLI path does not use this server yet; this proves the gateway mechanism.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK6 = HERE.parent / "Citi_P3_Zero_Trust_Backplane" / "zero_trust_task6_agent_identity"
sys.path.insert(0, str(TASK6 / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from zt_task6.keygen import generate_keypair, write_private_key
from zt_task6.registry import write_signed_registry
from zt_task6.revocation import revoke_agent_key
from zt_task6.rotation import rotate_agent_key
from zt_task6.signing import sign_request

AGENT = "transaction_monitoring_agent"
ROOT_ID = "gateway-demo-root"


class _Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"transaction_id": self.path.rsplit("/", 1)[-1], "amount": 120.0, "status": "posted"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _signed(key_path, tool="get_transaction"):
    return json.dumps(sign_request(AGENT, tool, key_path))


async def run(workdir: Path | None = None) -> list[dict]:
    """Run every scenario against a live gateway process; return one result row per scenario."""
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="gateway_demo_"))
    base.mkdir(parents=True, exist_ok=True)
    root = Ed25519PrivateKey.generate()
    root_private, root_public = base / "root-private.pem", base / "root-public.pem"
    root_private.write_bytes(root.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    root_public.write_bytes(root.public_key().public_bytes(serialization.Encoding.PEM,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo))

    def new_key(name):
        private_b64, public_b64 = generate_keypair()
        return write_private_key(name, private_b64, base / "keys"), public_b64

    good_path, good_pub = new_key("good")
    evil_path, _ = new_key("attacker")
    registry = base / "registry.yaml"
    write_signed_registry({AGENT: good_pub}, registry, root_private_key_path=root_private, root_key_id=ROOT_ID,
                          valid_from=datetime.now(timezone.utc) - timedelta(minutes=1))

    backend = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    audit = base / "gateway_audit.jsonl"
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / "mcp_server.py")], env={
        "MCP_MODE": "front",
        "BANKING_BACKEND_URL": f"http://127.0.0.1:{backend.server_address[1]}",
        "CITI_ZT_TASK6_SRC": str(TASK6 / "src"),
        "CITI_ZT_IDENTITY_REGISTRY": str(registry),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY": str(root_public),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID": ROOT_ID,
        "CITI_ZT_GATEWAY_AUDIT": str(audit),
    })
    rows: list[dict] = []

    async def call(session, label, expect_allowed, signed_identity):
        args = {"transaction_id": "TXN-1"}
        if signed_identity is not None:
            args["signed_identity"] = signed_identity
        result = await session.call_tool("get_transaction", arguments=args)
        text = result.content[0].text if result.content else ""
        allowed = (not result.isError) and "Transaction Details" in text
        rows.append({"scenario": label, "expected_allowed": expect_allowed, "allowed": allowed,
                     "ok": allowed == expect_allowed, "detail": text[:90]})

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                legit = _signed(good_path)
                await call(session, "1 legitimate agent", True, legit)
                await call(session, "2 wrong private key (impersonation)", False, _signed(evil_path))
                await call(session, "3 replay of request #1", False, legit)
                await call(session, "4 signature issued for a different tool", False,
                           _signed(good_path, "update_transaction_status"))
                await call(session, "5 no signature at all", False, None)
                revoke_agent_key(registry, root_private_key_path=root_private, root_key_id=ROOT_ID,
                                 agent_identity=AGENT, actor="demo-operator", reason="demo: suspected leak")
                await call(session, "6 right key, after revocation (gateway NOT restarted)", False, _signed(good_path))
                new_path, new_pub = new_key("rotated")
                rotate_agent_key(registry, root_private_key_path=root_private, root_key_id=ROOT_ID,
                                 agent_identity=AGENT, new_public_key=new_pub, grace_seconds=60)
                await call(session, "7 newly issued key after rotation", True, _signed(new_path))
    finally:
        backend.shutdown()
    events = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    for row in rows:
        row["gateway_events"] = [e["event_type"] for e in events]
    rows.append({"scenario": "gateway audit (written by the server process)", "events": [
        f'{e["event_type"]}:{e.get("reason") or "ok"}' for e in events]})
    return rows


if __name__ == "__main__":
    rows = asyncio.run(run())
    for row in rows[:-1]:
        mark = "OK " if row["ok"] else "FAIL"
        verdict = "ALLOWED" if row["allowed"] else "BLOCKED"
        print(f'[{mark}] {row["scenario"]:<58} -> {verdict}')
        if not row["allowed"]:
            print(f'        {row["detail"]}')
    print("\nGateway-side audit log (separate process):")
    for line in rows[-1]["events"]:
        print("  ", line)
    sys.exit(0 if all(r["ok"] for r in rows[:-1]) else 1)
