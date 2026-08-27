"""Latency benchmark for the native Zero Trust integration on the real MAS.

Answers the Work Plan's Task 7 performance questions -- PEP/PDP decision
latency, end-to-end workflow latency, Backplane overhead -- against the
*native* integration (real CLI message -> real ADK Runner -> real LLM ->
real agents -> real tools -> JSON), not a synthetic microbenchmark.

What this measures, and where each number comes from:

1. Bootstrap latency: constructing `NativeMasBackplane` directly (no ADK
   App, no LLM call needed for this part) -- Task 2 scan reuse check ->
   Task 1/2 reconciliation -> manifest merge -> Task 3 PEP placement ->
   Task 4 policy compile + in-process PDP startup (FastAPI app + health
   check) -> Task 5 Permit Lease component build. This is real work that
   happens once per `run_cli.py` invocation today (see
   NATIVE_ZERO_TRUST_README.md Part 2.3) -- it is not cached across
   process runs, only within one.

2. Per-boundary authorization latency: every `INPUT_AUTHORIZATION`,
   `AGENT_ROUTE_AUTHORIZATION`, `TOOL_AUTHORIZATION`, and
   `OUTPUT_AUTHORIZATION` event already carries its own
   `authorization_latency_ms`, timed in `engine.py`'s `authorize()` from
   just before building the signed request through the real Task 4 PDP
   call and Task 5 Permit Lease issuance. This script does not add new
   instrumentation -- it runs real scenarios and aggregates the numbers the
   enforcement engine already recorded, so the numbers are exactly what a
   real run pays, not an approximation of it.

3. End-to-end wall-clock latency per real message, and what fraction of it
   is Zero Trust decision time (sum of every authorization_latency_ms in
   that run) versus everything else (LLM generation, ADK bookkeeping,
   actual tool execution). This is the number that answers "is Backplane
   overhead measured and kept within an acceptable range."

4. Optional (--compare-baseline): the same messages run with Zero Trust on
   vs. off (`--no-zero-trust` equivalent), several trials each, to get an
   order-of-magnitude estimate of end-to-end overhead. Flagged as noisy:
   real LLM response time varies run to run by far more than the
   authorization overhead does, so this is a sanity check, not a precise
   measurement -- (2) and (3) are the precise numbers.

Usage:
    python latency_benchmark.py                       # default: 3 trials, warm bootstrap only
    python latency_benchmark.py --trials 5
    python latency_benchmark.py --include-cold-bootstrap   # also time a full Task 2 rescan (slow)
    python latency_benchmark.py --compare-baseline --trials 3
    python latency_benchmark.py --skip-critical-writes      # skip the two scenarios that touch the local DB
    python latency_benchmark.py --only front_office_faq

Every trial makes real LLM calls; this costs real time and tokens. A full
default run (3 trials x 5 scenarios = 15 real messages) takes on the order
of 10-20 minutes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from bank_adk.runner import run_message
from bank_adk.zero_trust import ZeroTrustConfig

# Same rationale as run_cli.py / verify_native_zero_trust_e2e.py: this
# script never spawns an MCP subprocess, so avoid the Windows
# ProactorEventLoop pipe-transport shutdown crash.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _backplane_root() -> Path:
    return _repo_root().parent / "Citi_P3_Zero_Trust_Backplane"


def _audit_path() -> Path:
    return _backplane_root() / "native_mas_runtime" / "output" / "native_mas_audit.jsonl"


def _read_audit_events() -> list[dict[str, Any]]:
    path = _audit_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


@dataclass
class Scenario:
    name: str
    message: str
    human_approval: bool = False
    secondary_authorization: bool = False
    change_ticket: str | None = None
    is_critical_write: bool = False


SCENARIOS: list[Scenario] = [
    Scenario(
        name="front_office_faq",
        message="What is the bank's policy on a lost debit card?",
    ),
    Scenario(
        name="unauthorized_transfer_blocked",
        message="Please transfer 50 dollars from account ACC-1001 to account ACC-1002 right now.",
        is_critical_write=True,
    ),
    Scenario(
        name="mid_office_representative_assist",
        message="Internal representative here, I need the financial profile and any "
        "recommended products or loan options for customer CUST-001 so I can assist them.",
    ),
    Scenario(
        name="back_office_eod_authorized",
        message="Please run the scheduled end-of-day batch processing for batch "
        "EOD-2026-04-21-CLEAN, including posting the ledger update.",
        human_approval=True,
        secondary_authorization=True,
        change_ticket="CHG-LATENCY-BENCH",
        is_critical_write=True,
    ),
]


# ---------------------------------------------------------------------------
# 1. Bootstrap latency (no LLM call)
# ---------------------------------------------------------------------------

def _measure_bootstrap(*, refresh: bool) -> float:
    """Time constructing NativeMasBackplane directly -- the Task 1-5
    pipeline that runs once per run_cli.py invocation. No ADK App, no LLM
    call: this isolates the Backplane's own one-time setup cost from
    agent/model latency entirely."""
    backplane_root = _backplane_root()
    if str(backplane_root) not in sys.path:
        sys.path.insert(0, str(backplane_root))
    from native_mas_runtime import NativeMasBackplane  # noqa: PLC0415

    output_dir = backplane_root / "native_mas_runtime" / "output"
    started = time.perf_counter()
    engine = NativeMasBackplane(
        backplane_root=backplane_root,
        mas_repo_root=_repo_root(),
        output_dir=output_dir,
        mode="enforce",
        refresh=refresh,
        runtime_agent_edges=[],
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    engine.close()
    return elapsed_ms


# ---------------------------------------------------------------------------
# 2 & 3. Per-boundary authorization latency + end-to-end wall clock
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    scenario: str
    wall_ms: float
    zt_ms_total: float
    zt_event_count: int
    by_boundary: dict[str, list[float]]


async def _run_once(scenario: Scenario) -> RunResult:
    config = ZeroTrustConfig.build(
        enabled=True,
        backplane_root=_backplane_root(),
        mas_repo_root=_repo_root(),
        mode="enforce",
        human_approval=scenario.human_approval,
        secondary_authorization=scenario.secondary_authorization,
        change_ticket=scenario.change_ticket,
    )
    started = time.perf_counter()
    await run_message(scenario.message, zero_trust_config=config)
    wall_ms = (time.perf_counter() - started) * 1000.0

    events = _read_audit_events()
    by_boundary: dict[str, list[float]] = {}
    for e in events:
        latency = e.get("authorization_latency_ms")
        if latency is None:
            continue
        by_boundary.setdefault(e["event_type"], []).append(float(latency))

    zt_ms_total = sum(v for values in by_boundary.values() for v in values)
    zt_event_count = sum(len(values) for values in by_boundary.values())
    return RunResult(scenario.name, wall_ms, zt_ms_total, zt_event_count, by_boundary)


async def _run_baseline_once(scenario: Scenario) -> float:
    """Same message, Zero Trust entirely disabled (zero_trust_config=None) --
    the real un-audited path, for the optional overhead comparison."""
    started = time.perf_counter()
    await run_message(scenario.message, zero_trust_config=None)
    return (time.perf_counter() - started) * 1000.0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min_ms": round(min(values), 3),
        "mean_ms": round(statistics.mean(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(_pct(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def _print_table(title: str, rows: dict[str, dict[str, float]]) -> None:
    print(f"\n{title}")
    print(f"  {'boundary':<28}{'n':>5}{'min':>10}{'mean':>10}{'median':>10}{'p95':>10}{'max':>10}   (ms)")
    for name, s in rows.items():
        if s.get("count", 0) == 0:
            print(f"  {name:<28}{'0':>5}   (no samples)")
            continue
        print(
            f"  {name:<28}{s['count']:>5}{s['min_ms']:>10.2f}{s['mean_ms']:>10.2f}"
            f"{s['median_ms']:>10.2f}{s['p95_ms']:>10.2f}{s['max_ms']:>10.2f}"
        )


async def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3, help="Repetitions per scenario (default: 3).")
    ap.add_argument("--only", default=None, help="Comma-separated scenario names to run.")
    ap.add_argument("--skip-critical-writes", action="store_true",
                     help="Skip scenarios that call apply_ledger_updates/execute_transfer.")
    ap.add_argument("--include-cold-bootstrap", action="store_true",
                     help="Also time a full Task 2 rescan (--zt-refresh equivalent). Slow: "
                     "re-scans the entire Citi_P3 source tree.")
    ap.add_argument("--compare-baseline", action="store_true",
                     help="Also run each scenario with Zero Trust fully disabled, to estimate "
                     "end-to-end overhead. Noisy: LLM response time dominates and varies run "
                     "to run far more than authorization overhead does.")
    args = ap.parse_args()

    names = set(args.only.split(",")) if args.only else None
    scenarios = [
        s for s in SCENARIOS
        if (names is None or s.name in names)
        and not (args.skip_critical_writes and s.is_critical_write)
    ]
    if not scenarios:
        print("No scenarios selected.", file=sys.stderr)
        return 2

    report: dict[str, Any] = {}

    # --- 1. Bootstrap latency ------------------------------------------------
    print("=== 1. Bootstrap latency (NativeMasBackplane construction, no LLM call) ===")
    warm_ms = _measure_bootstrap(refresh=False)
    print(f"  warm (Task 2 scan cache reused): {warm_ms:.1f} ms")
    report["bootstrap_warm_ms"] = round(warm_ms, 1)
    if args.include_cold_bootstrap:
        cold_ms = _measure_bootstrap(refresh=True)
        print(f"  cold (--zt-refresh, full Task 2 rescan of Citi_P3 source tree): {cold_ms:.1f} ms")
        report["bootstrap_cold_ms"] = round(cold_ms, 1)
    print("  (this cost is paid once per run_cli.py invocation today -- see")
    print("   NATIVE_ZERO_TRUST_README.md Part 2.3 for why it isn't cached across processes)")

    # --- 2 & 3. Per-scenario live runs ---------------------------------------
    print(f"\n=== 2/3. Live runs: {len(scenarios)} scenario(s) x {args.trials} trial(s) ===")
    all_results: list[RunResult] = []
    by_boundary_all: dict[str, list[float]] = {}
    for scenario in scenarios:
        print(f"\n--- {scenario.name} ---")
        print(f"message: {scenario.message!r}")
        for trial in range(1, args.trials + 1):
            result = await _run_once(scenario)
            all_results.append(result)
            for boundary, values in result.by_boundary.items():
                by_boundary_all.setdefault(boundary, []).extend(values)
            overhead_pct = (result.zt_ms_total / result.wall_ms * 100.0) if result.wall_ms else 0.0
            print(
                f"  trial {trial}/{args.trials}: wall={result.wall_ms:.0f}ms  "
                f"zt_decisions={result.zt_event_count}  zt_total={result.zt_ms_total:.1f}ms  "
                f"(zt = {overhead_pct:.2f}% of wall clock)"
            )

    boundary_stats = {name: _summarize(values) for name, values in sorted(by_boundary_all.items())}
    _print_table("Per-boundary authorization latency (real Task 4 PDP + Task 5 Permit "
                 "Lease issuance, per decision, across all scenarios/trials)", boundary_stats)
    report["authorization_latency_by_boundary_ms"] = boundary_stats

    wall_by_scenario: dict[str, list[float]] = {}
    for r in all_results:
        wall_by_scenario.setdefault(r.scenario, []).append(r.wall_ms)
    wall_stats = {name: _summarize(values) for name, values in wall_by_scenario.items()}
    _print_table("End-to-end wall-clock latency per scenario (dominated by real LLM calls)", wall_stats)
    report["wall_clock_by_scenario_ms"] = wall_stats

    total_wall = sum(r.wall_ms for r in all_results)
    total_zt = sum(r.zt_ms_total for r in all_results)
    overhead_pct = (total_zt / total_wall * 100.0) if total_wall else 0.0
    print(f"\nAggregate: {len(all_results)} real runs, {total_wall:.0f}ms total wall clock, "
          f"{total_zt:.1f}ms total Zero Trust decision time ({overhead_pct:.2f}% of wall clock).")
    report["aggregate"] = {
        "runs": len(all_results),
        "total_wall_ms": round(total_wall, 1),
        "total_zero_trust_ms": round(total_zt, 1),
        "zero_trust_pct_of_wall_clock": round(overhead_pct, 3),
    }

    # --- 4. Optional on/off comparison ---------------------------------------
    if args.compare_baseline:
        print("\n=== 4. Zero Trust ON vs OFF wall-clock comparison (noisy -- LLM latency "
              "varies far more than authorization overhead) ===")
        comparison: dict[str, Any] = {}
        for scenario in scenarios:
            on_times = [r.wall_ms for r in all_results if r.scenario == scenario.name]
            off_times = []
            for trial in range(1, args.trials + 1):
                off_ms = await _run_baseline_once(scenario)
                off_times.append(off_ms)
            on_stats, off_stats = _summarize(on_times), _summarize(off_times)
            delta_median = on_stats["median_ms"] - off_stats["median_ms"]
            print(f"\n  {scenario.name}:")
            print(f"    ON  median={on_stats['median_ms']:.0f}ms  (n={on_stats['count']})")
            print(f"    OFF median={off_stats['median_ms']:.0f}ms  (n={off_stats['count']})")
            print(f"    delta (median ON - median OFF): {delta_median:+.0f}ms")
            comparison[scenario.name] = {"on": on_stats, "off": off_stats, "delta_median_ms": round(delta_median, 1)}
        report["on_vs_off_comparison"] = comparison

    report_path = _repo_root() / "native_zero_trust_latency_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nFull report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
