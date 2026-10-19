"""A real ADK agent run whose MCP tool call is verified by the separate MCP gateway process.

Joins the two halves: the Zero Trust plugin signs the agent's tool call, the call travels over MCP,
and mcp_server.py (a separate process) verifies the identity before running the tool.

    python demo_agents_through_gateway.py

Needs the model API key in .env, the scanner output (agents_draft.yaml), and the zt_task6 package.
A tiny stub stands in for the banking backend so only the identity path is under test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BP = ROOT / "Citi_P3_Zero_Trust_Backplane"
TASK6_SRC = BP / "zero_trust_task6_agent_identity" / "src"
sys.path.insert(0, str(TASK6_SRC))
load_dotenv(HERE / ".env")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bank_adk.runner import run_message
from bank_adk.zero_trust import ZeroTrustConfig
from zt_task6.keygen import generate_keypair, write_private_key
from zt_task6.registry import write_signed_registry

ROOT_ID = "gateway-agent-root"
ROOT_AGENT = "user_proxy_agent"  # the agent that owns the MCP tools in this app
MESSAGE = ("You, the user proxy agent, must call your own get_transaction tool with transaction_id TXN-1001 and report its amount. "
           "Do not transfer to any other agent.")


class _Backend(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        _Backend.hits.append(self.path)
        body = json.dumps({"transaction_id": self.path.rsplit("/", 1)[-1], "amount": 120.0, "status": "posted"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def _events(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


async def _run(base: Path, key_for_agent: Path, label: str, tries: int = 3):
    audit = base / f"gateway_audit_{label}.jsonl"
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "citi_banking_mcp_server" / "mcp_server.py")], env={
        "MCP_MODE": "front", "BANKING_BACKEND_URL": base.joinpath("backend_url").read_text(),
        "CITI_ZT_TASK6_SRC": str(TASK6_SRC),
        "CITI_ZT_IDENTITY_REGISTRY": str(base / "registry.yaml"),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY": str(base / "root-public.pem"),
        "CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID": ROOT_ID, "CITI_ZT_GATEWAY_AUDIT": str(audit)})
    keymap = base / f"keymap_{label}.json"
    keymap.write_text(json.dumps({ROOT_AGENT: str(key_for_agent)}))
    for attempt in range(1, tries + 1):
        _Backend.hits.clear()
        if audit.exists():
            audit.unlink()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                config = ZeroTrustConfig.build(
                    enabled=True, backplane_root=BP, mas_repo_root=ROOT, output_dir=base / f"zt_{label}",
                    agent_key_map_path=keymap, identity_registry_path=base / "registry.yaml",
                    identity_registry_root_public_key_path=base / "root-public.pem",
                    identity_registry_root_key_id=ROOT_ID)
                await run_message(MESSAGE, mcp_session=session, mcp_tools=tools, compact=True, zero_trust_config=config)
        engine_audit = _events(base / f"zt_{label}" / "native_mas_audit.jsonl")
        gateway = _events(audit)
        reached = bool(_Backend.hits) or any(e["target_tool"] == "get_transaction" for e in gateway)
        # Only the root agent calling get_transaction counts; other agents have no key here and are denied.
        engine_denied = any(e.get("event_type") == "IDENTITY_VERIFICATION_FAILED"
                            and e.get("caller_runtime_id") == ROOT_AGENT and e.get("target_runtime_id") == "get_transaction"
                            for e in engine_audit)
        if reached or engine_denied:
            return {"gateway": [f'{e["event_type"]} {e.get("caller_runtime_id")} -> {e["target_tool"]}' for e in gateway],
                    "backend_hits": list(_Backend.hits), "engine_denied": engine_denied,
                    "engine_tool_events": [e["event_type"] for e in engine_audit if e.get("event_type") in ("TOOL_AUTHORIZATION", "IDENTITY_VERIFICATION_FAILED")]}
        print(f"  (model did not call the MCP tool; retry {attempt}/{tries})")
    return {"gateway": [], "backend_hits": [], "engine_denied": False, "engine_tool_events": [], "never_called": True}


async def main():
    base = Path(tempfile.mkdtemp(prefix="agents_gateway_"))
    root = Ed25519PrivateKey.generate()
    (base / "root-private.pem").write_bytes(root.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (base / "root-public.pem").write_bytes(root.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    priv, pub = generate_keypair()
    good = write_private_key("good", priv, base / "keys")
    evil = write_private_key("evil", generate_keypair()[0], base / "keys")
    write_signed_registry({ROOT_AGENT: pub}, base / "registry.yaml", root_private_key_path=base / "root-private.pem",
                          root_key_id=ROOT_ID, valid_from=datetime.now(timezone.utc) - timedelta(minutes=1))
    backend = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    (base / "backend_url").write_text(f"http://127.0.0.1:{backend.server_address[1]}")
    os.environ["BANKING_ADK_RUNTIME_DIR"] = str(base / "runtime")

    print("== RUN 1: agent with its OWN key calls get_transaction through the MCP gateway ==")
    ok = await _run(base, good, "ok")
    print("  gateway (separate process):", ok["gateway"] or "no event")
    print("  backend requests reached:  ", ok["backend_hits"] or "none")
    print("\n== RUN 2: agent holding the WRONG key ==")
    bad = await _run(base, evil, "bad")
    print("  engine decision:           ", bad["engine_tool_events"] or "none")
    print("  gateway (separate process):", bad["gateway"] or "no event (request never left the agent process)")
    print("  backend requests reached:  ", bad["backend_hits"] or "none")
    backend.shutdown()
    passed = bool(ok["gateway"]) and ok["gateway"][0].startswith("IDENTITY_VERIFIED") and ok["backend_hits"] \
        and bad["engine_denied"] and not bad["backend_hits"]
    print("\nRESULT:", "PASS - agent call verified by the gateway process; wrong key stopped, backend untouched" if passed else "CHECK OUTPUT ABOVE")
    sys.exit(0 if passed else 1)


def _quiet_teardown(loop, context):
    # Harmless SSL teardown race after the real work is done (see run_cli.py); swallow only that.
    exc = context.get("exception")
    if isinstance(exc, (OSError, RuntimeError)) or "SSL transport" in context.get("message", ""):
        return
    loop.default_exception_handler(context)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_quiet_teardown)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
