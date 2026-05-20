import argparse
import asyncio
import json

from dotenv import load_dotenv

load_dotenv()

from bank_adk.observability import flush_observability, init_observability, start_batch_run
from bank_adk.runner import run_message



def _ser(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


async def main():
    init_observability()

    ap = argparse.ArgumentParser()
    ap.add_argument('--message', required=True)
    ap.add_argument('--raw', action='store_true', help='Print the full raw debug trace.')
    args = ap.parse_args()

    with start_batch_run('run_cli'):
        result = await run_message(args.message, compact=not args.raw, scenario_name='cli_request')

    flush_observability()
    print(json.dumps(_ser(result), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
