import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from zt_task6.keygen import generate_keypair, write_private_key
from zt_task6.registry import RegistryError
from zt_task6.rotation import rotate_agent_key


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rotate an agent key with an overlap (grace) period.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--registry-root-private-key", type=Path, required=True)
    parser.add_argument("--registry-root-key-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--keys-dir", type=Path, required=True)
    parser.add_argument("--grace-seconds", type=int, default=300,
                        help="How long the old key keeps verifying (default 300).")
    args = parser.parse_args(argv)
    private_b64, public_b64 = generate_keypair()
    try:
        new_path = write_private_key(f"{args.agent_id}.next", private_b64, args.keys_dir)
        key_id = rotate_agent_key(
            args.registry, root_private_key_path=args.registry_root_private_key,
            root_key_id=args.registry_root_key_id, agent_identity=args.agent_id,
            new_public_key=public_b64, grace_seconds=args.grace_seconds,
        )
    except (RegistryError, OSError) as exc:
        parser.exit(1, f"Rotation failed (the new key is not trusted unless registered): {exc}\n")
    print(f"ROTATED {args.agent_id}: new key {key_id}; old key valid for {args.grace_seconds}s more.")
    print(f"New private key: {new_path}")
    print(f"Swap it in as {args.keys_dir / (args.agent_id + '.key')} (or update the agent key map) within the grace period.")


if __name__ == "__main__":
    main()
