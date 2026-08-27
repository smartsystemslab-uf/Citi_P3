# Latency Benchmark Results — Native Zero Trust Integration

Snapshot from `python latency_benchmark.py` (see that file for what exactly
is measured and how). All numbers below are from real, live runs: real ADK
Runner, real LLM calls, real Task 1-5 Backplane engine, real signed permits
— not synthetic or mocked. Re-run the script yourself for current numbers;
this file is a point-in-time record, not a live dashboard.

Run date: 2026-08-27. 12 real runs (4 scenarios x 3 trials) plus a separate
one-off cold-bootstrap measurement.

## 1. Bootstrap latency (paid once per `run_cli.py` invocation, no LLM call)

| | latency |
|---|---|
| Warm (Task 2 scan cache reused) | **~1.73s** (1727-1751ms across 2 measurements) |
| Cold (`--zt-refresh`, full Task 2 rescan of the entire `Citi_P3` source tree) | **4238ms** |

This is real, non-trivial, fixed overhead: Task 1/2 reconciliation, manifest
merge, Task 3 PEP placement generation, Task 4 policy compilation + starting
an in-process FastAPI PDP app + health check, and Task 5 Permit Lease
component construction, all run fresh on every invocation (see
`NATIVE_ZERO_TRUST_README.md` Part 2.3 for why it isn't cached across
processes). It does not scale with the message being processed — it's the
same cost whether the message needs one agent or ten.

## 2. Per-boundary authorization latency (real Task 4 PDP decision + Task 5 Permit Lease issuance, per decision)

12 runs, 158 real authorization decisions total:

| boundary | n | min | mean | median | p95 | max | (ms) |
|---|---:|---:|---:|---:|---:|---:|---|
| `AGENT_ROUTE_AUTHORIZATION` | 96 | 8.06 | 9.49 | 9.37 | 10.54 | 11.05 | |
| `INPUT_AUTHORIZATION` | 12 | 8.93 | 9.79 | 9.93 | 10.42 | 10.58 | |
| `OUTPUT_AUTHORIZATION` | 12 | 8.83 | 9.45 | 9.41 | 10.20 | 10.66 | |
| `TOOL_AUTHORIZATION` | 38 | 8.95 | 9.95 | 9.98 | 11.09 | 11.29 | |

**Every boundary type costs about the same, ~8-11ms per decision**,
regardless of whether it's gating a read-only FAQ lookup or a critical
ledger write — the cost is dominated by the fixed overhead of one signed
PDP round-trip + one signed Permit Lease issuance (both in-process), not by
the complexity of the policy being evaluated.

## 3. End-to-end wall-clock latency per scenario (dominated by real LLM calls)

| scenario | n | min | mean | median | p95 | max | (ms) |
|---|---:|---:|---:|---:|---:|---:|---|
| `front_office_faq` | 3 | 5895 | 7122 | 6892 | 8411 | 8580 | |
| `unauthorized_transfer_blocked` | 3 | 5560 | 6296 | 6310 | 6946 | 7017 | |
| `mid_office_representative_assist` | 3 | 12419 | 14084 | 14865 | 14957 | 14967 | |
| `back_office_eod_authorized` | 3 | 11155 | 13126 | 11401 | 16281 | 16823 | |

Scales with how many real LLM calls the scenario chains (front-office FAQ:
~4 LLM calls; back-office EOD: ~7 sequential LLM calls plus 2 real tool
calls) — this is agent/LLM latency, not Zero Trust latency.

## 4. Zero Trust overhead as a fraction of end-to-end latency

**Aggregate across all 12 runs: 121,883ms total wall clock, 1,519.9ms total
Zero Trust decision time — 1.25% of wall clock.**

Per scenario, the range was 0.98%-1.66%. This directly answers the Work
Plan's Task 7 target "Backplane overhead: measured and kept within an
acceptable range" for the native integration: at the current scale (single
request, in-process PDP), enforcement overhead is roughly two orders of
magnitude smaller than the real LLM latency it rides alongside.

## What this does not measure yet

- **Throughput under concurrency** ("Performance under increasing request
  concurrency" in the Work Plan) — every run above was sequential, one
  message at a time. The in-process `TestClient`-backed PDP and the
  per-invocation bootstrap are both plausible bottlenecks under concurrent
  load that this script does not exercise.
- **Revocation propagation delay** and **policy update time** — not
  measured here; would need a dedicated scenario that issues a revocation
  or policy change mid-run and times how long it takes to take effect.
- **Scaling with number of agents** — all scenarios use the existing
  ~30-agent tree; this doesn't show how bootstrap or per-decision latency
  would change on a larger deployment.
- The bootstrap number is measured by constructing `NativeMasBackplane`
  directly, bypassing the ADK `App`/root-agent construction that
  `run_cli.py` also pays for on every invocation (building ~30 `LlmAgent`
  objects) — so it's a lower bound on `run_cli.py`'s true fixed overhead,
  not the whole of it.
