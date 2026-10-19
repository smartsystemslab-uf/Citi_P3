import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from zt_task6.registry import RegistryError
from zt_task6.revocation import revoke_agent_key


def main(argv=None):
    parser = argparse.ArgumentParser(description="Immediately revoke an agent's registered key(s).")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--registry-root-private-key", type=Path, required=True)
    parser.add_argument("--registry-root-key-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--key-id", help="Revoke only this key (default: every active key of the agent).")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor", default=getpass.getuser(), help="Who is revoking (default: current user).")
    parser.add_argument("--audit-log", type=Path, help="JSONL file to append the IDENTITY_REVOKED event to.")
    args = parser.parse_args(argv)
    try:
        revoked = revoke_agent_key(
            args.registry, root_private_key_path=args.registry_root_private_key,
            root_key_id=args.registry_root_key_id, agent_identity=args.agent_id, key_id=args.key_id,
            actor=args.actor, reason=args.reason, audit_log_path=args.audit_log,
        )
    except (RegistryError, OSError) as exc:
        parser.exit(1, f"Revocation failed: {exc}\n")
    if not revoked:
        print(f"No active key to revoke for {args.agent_id} (already revoked).")
        return
    for key_id in revoked:
        print(f"REVOKED {args.agent_id} key {key_id} by {args.actor}")
    print("Running verifiers pick this up on their next registry reload; new processes see it at start.")
    print(f"{args.agent_id} cannot authenticate until a new key is registered (rotate_agent_key.py).")


if __name__ == "__main__":
    main()
