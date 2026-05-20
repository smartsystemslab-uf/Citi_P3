import argparse
import asyncio
import json

from dotenv import load_dotenv

load_dotenv()

from bank_adk.observability import flush_observability, init_observability, log_batch_evaluation_summary, start_batch_run
from bank_adk.runner import run_many
from bank_adk.scenarios import SCENARIOS


def _ser(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


async def main():
    init_observability()

    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', choices=[*SCENARIOS.keys(), 'all'], default='all')
    ap.add_argument('--raw', action='store_true', help='Print the full raw debug trace.')
    args = ap.parse_args()

    selected = SCENARIOS if args.scenario == 'all' else {args.scenario: SCENARIOS[args.scenario]}
    scenario_names = list(selected.keys())
    messages = list(selected.values())

    with start_batch_run('run_scenarios', scenario_names=scenario_names):
        results = await run_many(messages, compact=not args.raw, scenario_names=scenario_names)
        if not args.raw:
            log_batch_evaluation_summary(scenario_names, results)

    flush_observability()
    print(json.dumps({name: _ser(res) for name, res in zip(scenario_names, results)}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
