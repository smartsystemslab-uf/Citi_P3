"""Offline wiring check for native CITI-MAS Zero Trust integration.

This builds the real ADK agent tree and the real Backplane runtime, but it does
not send a model request.  It is intended to prove that the plugin is attached
to the native App and that generated PEPs exist for input/output, root routing,
and representative real tool calls before running an online end-to-end test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from bank_adk.app import create_app
from bank_adk.zero_trust import ZeroTrustConfig


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _plugin(app):
    for item in list(getattr(app, "plugins", None) or getattr(app, "_citi_zero_trust_plugins", None) or []):
        if getattr(item, "name", "") == "citi_zero_trust_backplane":
            return item
    return None


async def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--backplane-root", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = ZeroTrustConfig.build(
        enabled=True,
        backplane_root=args.backplane_root,
        mas_repo_root=_repo_root(),
        output_dir=args.output,
        mode="enforce",
        refresh=args.refresh,
    )
    app = create_app(zero_trust_config=config)
    plugin = _plugin(app)
    if plugin is None:
        raise RuntimeError("ZeroTrustBackplanePlugin was not attached to the native ADK App")

    probes = {
        "input_pep": plugin.engine.find_input_placement(),
        "output_pep": plugin.engine.find_output_placement(),
        "root_to_front": plugin.engine.find_route_placement("user_proxy_agent", "front_office_router"),
        "root_to_mid": plugin.engine.find_route_placement("user_proxy_agent", "mid_office_router"),
        "root_to_back": plugin.engine.find_route_placement("user_proxy_agent", "back_office_router"),
        "transaction_write": plugin.engine.find_tool_placement("transaction_processing_agent", "execute_transfer"),
        "eod_ledger_write": plugin.engine.find_tool_placement("ledger_update_agent", "apply_ledger_updates"),
    }
    missing = [name for name, value in probes.items() if value is None]
    result = {
        "native_app": getattr(app, "name", ""),
        "root_agent": getattr(app.root_agent, "name", ""),
        "plugin": plugin.name,
        "runtime_agent_edges": len(plugin.runtime_agent_edges),
        "input_pep_present": probes["input_pep"] is not None,
        "output_pep_present": probes["output_pep"] is not None,
        "root_routes_present": all(probes[key] is not None for key in ("root_to_front", "root_to_mid", "root_to_back")),
        "critical_tool_peps_present": probes["transaction_write"] is not None and probes["eod_ledger_write"] is not None,
        "logical_peps": len(plugin.engine.plan_json.get("logical_peps", [])),
        "physical_peps": len(plugin.engine.plan_json.get("physical_peps", [])),
        "policy_version": plugin.engine.task4_runtime.engine.bundle.version,
        "missing": missing,
        "status": "PASS" if not missing else "FAIL",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    await plugin.close()
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
