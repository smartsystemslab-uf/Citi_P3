import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from bank_adk.runner import run_message
from bank_adk.zero_trust import ZeroTrustConfig

# This CLI entrypoint never spawns an MCP subprocess (mcp_session is not
# wired here; see run_scenarios.py for that path), so it's safe to avoid
# asyncio's default Windows ProactorEventLoop. Proactor's IOCP pipe
# transports can run __del__/__repr__ during interpreter-shutdown garbage
# collection after their underlying handle is already gone, which crashes
# with "Windows fatal exception: access violation" (segfault, exit 139)
# right after the real result has already been printed. The Selector loop
# does not use those pipe transports and does not have this failure mode.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _ser(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _quiet_post_shutdown_ssl_errors(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Swallow the benign SSL-transport teardown race that fires after this
    script's real work is already done.

    litellm's aiohttp-backed client keeps its SSL connection's low-level
    teardown (a final TLS close_notify write) scheduled for later rather than
    finishing it synchronously inside close()/aclose() — even litellm's own
    __del__ handling treats this as an expected, unavoidable race (see
    AsyncHTTPHandler._aiohttp_session_bound_elsewhere in the litellm package).
    When that scheduled write finally runs, the loop this script used has
    already closed, so the write fails with "Bad file descriptor" and the
    loop's own cleanup of that failure then hits "Event loop is closed" —
    both harmless, both after the real result has already printed.
    """
    exc = context.get("exception")
    message = context.get("message", "")
    if isinstance(exc, (OSError, RuntimeError)) or "SSL transport" in message:
        return
    loop.default_exception_handler(context)


async def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", required=True)
    ap.add_argument("--raw", action="store_true", help="Print the full raw debug trace.")

    # Native end-to-end Zero Trust switches. These operate on the original CLI
    # -> ADK App -> real agents -> real tools -> final JSON pipeline.
    # Secure by default: enforcement is ON unless explicitly disabled. Zero
    # Trust that has to be remembered and opted into on every invocation is
    # not zero trust; an explicit --no-zero-trust is required to run
    # unprotected, so "I forgot the flag" can never be the reason a real
    # message reached the agents/tools without authorization.
    ap.add_argument(
        "--zero-trust",
        dest="zero_trust",
        action="store_true",
        default=True,
        help="Enable native Zero Trust Backplane enforcement (default: on).",
    )
    ap.add_argument(
        "--no-zero-trust",
        dest="zero_trust",
        action="store_false",
        help="Disable native Zero Trust Backplane enforcement. Requires explicit opt-out; NOT recommended outside local debugging/comparison runs.",
    )
    ap.add_argument("--zt-backplane-root", default=None, help="Path to Citi_P3_Zero_Trust_Backplane.")
    ap.add_argument("--zt-output", default=None, help="Directory for native Zero Trust runtime artifacts/audit.")
    ap.add_argument("--zt-agent-key-map", default=None, help="Operator-managed JSON mapping ADK agent names to private-key paths (or CITI_ZT_AGENT_KEY_MAP).")
    ap.add_argument("--zt-identity-registry", default=None, help="Root-signed Task 6 identity registry YAML (or CITI_ZT_IDENTITY_REGISTRY).")
    ap.add_argument("--zt-identity-registry-root-public-key", default=None, help="Independently provisioned registry root public PEM (or CITI_ZT_IDENTITY_REGISTRY_ROOT_PUBLIC_KEY).")
    ap.add_argument("--zt-identity-registry-root-key-id", default=None, help="Pinned registry root key ID (or CITI_ZT_IDENTITY_REGISTRY_ROOT_KEY_ID).")
    ap.add_argument("--zt-mode", choices=["enforce", "shadow"], default="enforce")
    ap.add_argument("--zt-refresh", action="store_true", help="Rescan/regenerate Task 2-5 runtime artifacts before this run.")
    ap.add_argument("--zt-human-approval", action="store_true", help="Supply a validated human-approval assertion to critical operations.")
    ap.add_argument("--zt-secondary-authorization", action="store_true", help="Supply an independent secondary-authorization assertion.")
    ap.add_argument("--zt-change-ticket", default=None, help="Change ticket used to satisfy critical-write obligations when required.")
    ap.add_argument(
        "--zt-shadow-allow-critical",
        action="store_true",
        help="In shadow mode only, allow critical writes that would otherwise be blocked. Off by default for safety.",
    )
    ap.add_argument(
        "--zt-status",
        action="store_true",
        help="Wrap the original JSON result with trace_id/audit metadata for demo verification.",
    )
    args = ap.parse_args()

    config = None
    if args.zero_trust:
        config = ZeroTrustConfig.build(
            enabled=True,
            backplane_root=args.zt_backplane_root,
            mas_repo_root=_repo_root(),
            output_dir=args.zt_output,
            mode=args.zt_mode,
            refresh=args.zt_refresh,
            human_approval=args.zt_human_approval,
            secondary_authorization=args.zt_secondary_authorization,
            change_ticket=args.zt_change_ticket,
            shadow_allow_critical=args.zt_shadow_allow_critical,
            agent_key_map_path=args.zt_agent_key_map,
            identity_registry_path=args.zt_identity_registry,
            identity_registry_root_public_key_path=args.zt_identity_registry_root_public_key,
            identity_registry_root_key_id=args.zt_identity_registry_root_key_id,
        )

    result = await run_message(
        args.message,
        compact=not args.raw,
        zero_trust_config=config,
        include_zero_trust_status=args.zt_status,
    )
    print(json.dumps(_ser(result), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_quiet_post_shutdown_ssl_errors)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
