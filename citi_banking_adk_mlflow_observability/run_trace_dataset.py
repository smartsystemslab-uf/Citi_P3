import argparse
import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import mlflow

from bank_adk.failure_scenarios import DEFAULT_FAILURE_TYPES, build_trace_cases, dataset_manifest
from bank_adk.observability import (
    flush_observability,
    init_observability,
    log_batch_evaluation_summary,
    log_dataset_manifest,
    log_dataset_run_summary,
    log_dataset_csv_tables,
    log_error_artifact,
    start_batch_run,
)
from bank_adk.runner import run_message
from bank_adk.scenarios import SCENARIOS


def _jsonable(obj: Any) -> Any:
    try:
        return json.loads(json.dumps(obj, default=str, ensure_ascii=False))
    except Exception:
        return str(obj)


async def main() -> None:
    init_observability()

    ap = argparse.ArgumentParser(
        description="Generate a LumiMAS-style trace dataset for the ADK banking multi-agent testbed."
    )
    ap.add_argument("--scenario", choices=[*SCENARIOS.keys(), "all"], default="all")
    ap.add_argument("--normal-repeats", type=int, default=5, help="Normal baseline repeats per workflow.")
    ap.add_argument("--failure-repeats", type=int, default=2, help="Repeats per failure type per workflow.")
    ap.add_argument(
        "--failure-types",
        default="all",
        help="Comma-separated failure types, or 'all'. Available: " + ",".join(DEFAULT_FAILURE_TYPES),
    )
    ap.add_argument("--max-cases", type=int, default=0, help="Optional smoke-test cap. 0 means run all generated cases.")
    ap.add_argument("--continue-on-error", action="store_true", default=True)
    ap.add_argument("--stop-on-error", action="store_true", help="Stop the dataset run on the first failed case.")
    args = ap.parse_args()

    selected_scenarios = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    failure_types = DEFAULT_FAILURE_TYPES if args.failure_types == "all" else tuple(
        item.strip() for item in args.failure_types.split(",") if item.strip()
    )

    cases = build_trace_cases(
        selected_scenarios=selected_scenarios,
        normal_repeats=args.normal_repeats,
        failure_repeats=args.failure_repeats,
        failure_types=failure_types,
    )
    if args.max_cases and args.max_cases > 0:
        cases = cases[: args.max_cases]

    manifest = dataset_manifest(cases)
    case_ids = [case.case_id for case in cases]

    completed_results: list[Any] = []
    completed_case_ids: list[str] = []
    failed_cases: list[dict[str, Any]] = []

    with start_batch_run("run_trace_dataset", scenario_names=case_ids):
        mlflow.set_tags(
            {
                "dataset.kind": "lumimas_style_trace_dataset",
                "dataset.normal_repeats": str(args.normal_repeats),
                "dataset.failure_repeats": str(args.failure_repeats),
                "dataset.failure_types": ",".join(failure_types),
            }
        )
        log_dataset_manifest(manifest)

        for idx, case in enumerate(cases, start=1):
            print(f"[{idx}/{len(cases)}] Running {case.case_id}")
            try:
                result = await run_message(case.message, compact=True, scenario_name=case.case_id)
                completed_results.append(result)
                completed_case_ids.append(case.case_id)
            except Exception as exc:  # noqa: BLE001 - log and continue for dataset generation
                failed_payload = log_error_artifact(
                    case_id=case.case_id,
                    message=case.message,
                    error=exc,
                    case_metadata=case.to_dict(),
                )
                failed_cases.append(
                    {
                        "case_id": case.case_id,
                        "base_scenario": case.base_scenario,
                        "split": case.split,
                        "failure_type": case.failure_type,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "logged_artifact": bool(failed_payload),
                    }
                )
                print(f"  ERROR: {type(exc).__name__}: {exc}")
                if args.stop_on_error:
                    raise

        eval_summary = log_batch_evaluation_summary(completed_case_ids, completed_results)
        table_summary = log_dataset_csv_tables(
            manifest=manifest,
            completed_case_ids=completed_case_ids,
            completed_results=completed_results,
            failed_cases=failed_cases,
        )
        run_summary = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "requested_case_count": len(cases),
            "completed_count": len(completed_results),
            "failed_count": len(failed_cases),
            "success_rate": round(len(completed_results) / len(cases), 4) if cases else 0.0,
            "failed_cases": failed_cases,
            "evaluation_summary": eval_summary,
            "dataset_tables_summary": table_summary,
        }
        log_dataset_run_summary(run_summary)

    flush_observability()
    print(json.dumps(_jsonable(run_summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
