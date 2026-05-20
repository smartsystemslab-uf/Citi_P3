import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from run_scenarios import SCENARIOS
from bank_adk.observability import (
    json_dumps,
    normalize_events,
    records_to_csv_rows,
    records_to_jsonl,
    stable_trace_id,
    summarize_records,
    summarize_runs,
)
from bank_adk.runner import run_message


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def try_log_to_mlflow(output_dir: Path, aggregate_summary: dict[str, Any], run_summaries: list[dict[str, Any]]) -> None:
    try:
        import mlflow  # type: ignore
    except Exception:
        print("MLflow is not installed; skipped MLflow logging.")
        return

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "citi_p3_log_observability_study"))

    with mlflow.start_run(run_name="log_observability_study"):
        overall = aggregate_summary.get("overall", {})
        for key, value in overall.items():
            if isinstance(value, (int, float)):
                mlflow.log_metric(key, float(value))
        mlflow.log_param("scenario_count", len(aggregate_summary.get("by_scenario", {})))
        mlflow.log_param("run_count", len(run_summaries))
        for artifact in [
            "aggregate_summary.json",
            "run_summaries.json",
            "selected_events.jsonl",
            "all_events.csv",
            "selected_events.csv",
        ]:
            p = output_dir / artifact
            if p.exists():
                mlflow.log_artifact(str(p))


def selected_scenarios(name: str) -> dict[str, str]:
    return SCENARIOS if name == "all" else {name: SCENARIOS[name]}


async def run_study(args: argparse.Namespace) -> None:
    load_dotenv()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario_map = selected_scenarios(args.scenario)
    all_records = []
    selected_records = []
    run_summaries = []

    for scenario_name, message in scenario_map.items():
        for run_index in range(1, args.repeats + 1):
            trace_id = stable_trace_id(scenario_name, run_index, message)
            print(f"Running scenario={scenario_name} repeat={run_index}/{args.repeats} trace_id={trace_id}")

            started = time.perf_counter()
            try:
                raw_events = await run_message(message, compact=False)
                status = "ok"
                error = ""
            except Exception as exc:
                raw_events = [
                    {
                        "author": "runner",
                        "actions": {},
                        "parts": [
                            {
                                "text": f"ERROR while running scenario {scenario_name}: {type(exc).__name__}: {exc}"
                            }
                        ],
                    }
                ]
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            runtime = time.perf_counter() - started

            records = normalize_events(
                raw_events,
                scenario_name=scenario_name,
                run_index=run_index,
                message=message,
                trace_id=trace_id,
                threshold=args.threshold,
            )
            summary = summarize_records(records)
            summary.update(
                {
                    "scenario_name": scenario_name,
                    "run_index": run_index,
                    "trace_id": trace_id,
                    "status": status,
                    "error": error,
                    "runtime_seconds": round(runtime, 4),
                }
            )
            run_summaries.append(summary)
            all_records.extend(records)
            selected_records.extend([r for r in records if r.selected_for_monitoring])

            print(
                json_dumps(
                    {
                        "total_events": summary["total_events"],
                        "selected_events": summary["selected_events"],
                        "raw_size_bytes": summary["raw_size_bytes"],
                        "selected_size_bytes": summary["selected_size_bytes"],
                        "storage_reduction_rate": summary["storage_reduction_rate"],
                        "critical_event_recall": summary["critical_event_recall"],
                        "runtime_seconds": summary["runtime_seconds"],
                        "status": status,
                    }
                )
            )

    aggregate = summarize_runs(run_summaries)

    write_json(out_dir / "aggregate_summary.json", aggregate)
    write_json(out_dir / "run_summaries.json", run_summaries)
    (out_dir / "selected_events.jsonl").write_text(records_to_jsonl(selected_records, include_payload=args.include_payload), encoding="utf-8")
    (out_dir / "all_events.jsonl").write_text(records_to_jsonl(all_records, include_payload=args.include_payload), encoding="utf-8")
    write_csv(out_dir / "all_events.csv", records_to_csv_rows(all_records))
    write_csv(out_dir / "selected_events.csv", records_to_csv_rows(selected_records))

    if args.mlflow:
        try_log_to_mlflow(out_dir, aggregate, run_summaries)

    print("\nLog Observability Study complete.")
    print(f"Output directory: {out_dir.resolve()}")
    print(json.dumps(aggregate.get("overall", aggregate), indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Citi P3 Log Observability Study.")
    parser.add_argument("--scenario", choices=[*SCENARIOS.keys(), "all"], default="all")
    parser.add_argument("--repeats", type=int, default=3, help="Number of times to run each scenario.")
    parser.add_argument("--threshold", type=float, default=0.45, help="Importance threshold for selected monitoring events.")
    parser.add_argument("--output-dir", default="observability_runs/latest")
    parser.add_argument("--include-payload", action="store_true", help="Include full payloads in JSONL outputs. Use carefully.")
    parser.add_argument("--mlflow", action="store_true", help="Log summary metrics and artifacts to MLflow if installed.")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run_study(parse_args()))
