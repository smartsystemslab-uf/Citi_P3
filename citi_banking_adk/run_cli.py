import argparse
import asyncio
import json

from dotenv import load_dotenv

from bank_adk.runner import run_message


def _ser(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


async def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument('--message', required=True)
    ap.add_argument('--raw', action='store_true', help='Print the full raw debug trace.')
    args = ap.parse_args()
    print(json.dumps(_ser(await run_message(args.message, compact=not args.raw)), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
