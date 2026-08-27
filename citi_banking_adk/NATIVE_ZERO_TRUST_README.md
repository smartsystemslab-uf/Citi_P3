# Native Zero Trust Integration — How to Run It, and How It Actually Works

This document covers the **native** integration: a real Zero Trust Backplane
plugin attached directly to the real `google-adk` `App`/`Runner`, protecting
the actual CITI-MAS multi-agent system as it runs — real CLI message in,
real LLM-driven agent routing, real tool calls, real JSON out.

For a precise file-by-file record of what was already here versus what was
fixed/added, see [`NATIVE_ZERO_TRUST_CHANGELOG.md`](NATIVE_ZERO_TRUST_CHANGELOG.md).

It is a different thing from `integrated_demo/` in the Backplane repo, which
is a harness that calls the PEP/PDP/Permit-Lease chain and the original Citi
tools itself, in an order the harness script decides. That approach proves
the Task 1-5 components work; it does not prove they are wired into the live
multi-agent system's own decision-making. This document is about the
approach that does. See
[`../../Citi_P3_Zero_Trust_Backplane/native_mas_runtime/NATIVE_TASK6_INTEGRATION_PROOF.md`](../../Citi_P3_Zero_Trust_Backplane/native_mas_runtime/NATIVE_TASK6_INTEGRATION_PROOF.md)
for the bug-hunt narrative and live-run evidence behind everything described here.

---

## Part 1 — How to run it

### 1.1 Repo layout requirement

This only works with both repos checked out as siblings, because
`ZeroTrustConfig` auto-detects the Backplane root by walking up from the MAS
repo and looking for a sibling directory:

```
D:\Lab\
├── Citi_P3\                          <- the MAS (this repo)
│   └── citi_banking_adk\
│       ├── run_cli.py
│       ├── verify_native_zero_trust_e2e.py
│       └── bank_adk\zero_trust\
└── Citi_P3_Zero_Trust_Backplane\     <- the Backplane
    └── native_mas_runtime\
        ├── engine.py
        └── output\                  <- generated at runtime
```

If your Backplane checkout lives somewhere else, every command below accepts
`--zt-backplane-root PATH` (or set `CITI_ZT_BACKPLANE_ROOT`).

### 1.2 One-time setup

```powershell
cd D:\Lab\Citi_P3\citi_banking_adk
py -3.11 -m venv .venv          # if not already created
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt   # or however this project's deps are installed
```

Two things are easy to miss:

- **No `pip install` of the Backplane packages is needed in this venv.**
  The plugin injects the Backplane's `zero_trust_task3/4/5` and
  `interface_standard` `src/` directories straight onto `sys.path` at
  runtime (see Part 2.4). What *is* required is that this venv already has
  the raw libraries those packages import — `cryptography`, `httpx`,
  `pydantic`, `PyYAML`, `fastapi` — which it will, because `google-adk` pulls
  in the same family of dependencies. If you see
  `ModuleNotFoundError: No module named 'cryptography'` (or similar), install
  it directly into this venv; it does **not** mean the Backplane packages
  need `pip install -e`.
- **A real LLM key is required.** `.env` needs `UF_NAVIGATOR_API_KEY` /
  `UF_NAVIGATOR_BASE_URL` / `UF_NAVIGATOR_MODEL` (or whatever this checkout's
  `bank_adk/llm.py` reads) set to a real, working endpoint. Every command
  below makes a real LLM call — there is no offline/mocked mode for
  `run_cli.py` or `verify_native_zero_trust_e2e.py` (for a mocked-plugin
  unit-test mode, see `tests_native_zt/` instead).

### 1.3 Run a protected message through the real MAS

```powershell
python run_cli.py --message "What is the bank's policy on a lost debit card?"
```

That's it — **Zero Trust is on by default.** No flag needed. This sends the
message through the real `google.adk` `App`/`Runner`, lets the real LLM
decide which agents and tools to invoke, and the native plugin authorizes
every hop.

To see the trace metadata (trace id, workflow instance id, audit file path,
policy version) alongside the result:

```powershell
python run_cli.py --message "..." --zt-status
```

### 1.4 Full flag reference

| Flag | Default | Meaning |
|---|---|---|
| `--message TEXT` | required | The user message, exactly as if typed at a real CLI/chat front end. |
| `--zero-trust` / `--no-zero-trust` | **on** | Enforcement is on unless you explicitly pass `--no-zero-trust`. Use the opt-out only for local debugging/comparison — never in anything resembling production. |
| `--zt-backplane-root PATH` | sibling dir | Where to find `Citi_P3_Zero_Trust_Backplane`. |
| `--zt-output PATH` | `<backplane>/native_mas_runtime/output` | Where generated artifacts and the audit log land. |
| `--zt-mode {enforce,shadow}` | `enforce` | `enforce` blocks denied actions for real. `shadow` records what *would* happen without blocking (see 2.7). |
| `--zt-refresh` | off | Force Task 2 to re-scan `Citi_P3`'s source tree and regenerate the Task 1-5 artifacts before this run, instead of reusing the cached scan. Use after changing agent/tool code. |
| `--zt-human-approval` | off | Supplies a signed human-approval assertion for critical operations. |
| `--zt-secondary-authorization` | off | Supplies an independent second authorizer's assertion. |
| `--zt-change-ticket ID` | none | Satisfies the `require_change_ticket` obligation some critical-write policies attach. |
| `--zt-shadow-allow-critical` | off | In shadow mode only, also simulate allowing critical writes that enforce mode would block. |
| `--zt-status` | off | Wrap the result with `trace_id` / `workflow_instance_id` / `audit_path` / `policy_version` metadata. |
| `--raw` | off | Print the full raw ADK debug event stream instead of the compact trace. |

### 1.5 Example: a critical write, unauthorized (blocked) vs. authorized (allowed)

Blocked — no approval context supplied, real critical write, real PDP `MANUAL_REVIEW`:

```powershell
python run_cli.py --message "Please transfer 50 dollars from account ACC-1001 to account ACC-1002 right now." --zt-status
```

Allowed — the same category of request, this time with everything the policy requires:

```powershell
python run_cli.py `
  --message "Please run the scheduled end-of-day batch processing for batch EOD-2026-04-21-CLEAN, including posting the ledger update." `
  --zt-human-approval --zt-secondary-authorization --zt-change-ticket "CHG-1234" --zt-status
```

The second one really does write to the local sqlite dev database
(`bank_adk/.runtime/banking.db`) — it is a real state-changing operation,
not a simulation, once Zero Trust allows it through.

### 1.6 Verify it yourself, automatically

Don't take any of the above on faith — run the live verification suite:

```powershell
python verify_native_zero_trust_e2e.py
```

This runs five real scenarios (front-office authorized read, front-office
unauthorized transfer blocked, mid-office parallel workflow, back-office
critical write blocked, back-office critical write allowed), grades every
one against the plugin's own audit trail (not the LLM's wording, which is
non-deterministic), and prints `OVERALL: PASS` / `FAIL` with exit code
`0`/`1`. A full JSON report lands at `native_zero_trust_e2e_report.json`.
Takes 2-4 minutes and makes real LLM calls.

```powershell
python verify_native_zero_trust_e2e.py --only front_office_faq          # run one scenario
python verify_native_zero_trust_e2e.py --skip-critical-writes            # skip the two that touch the local DB
```

See Part 3 for what each scenario actually proves and why it's built this way.

### 1.7 Measuring latency

```powershell
python latency_benchmark.py
```

Runs the same kind of real live scenarios and reports bootstrap latency
(the one-time Task 1-5 pipeline cost), per-boundary authorization latency
(real PDP decision + Permit Lease issuance, from the audit trail's own
timing), end-to-end wall-clock latency, and what fraction of the total is
Zero Trust overhead versus real LLM/agent time. See
[`LATENCY_BENCHMARK_RESULTS.md`](LATENCY_BENCHMARK_RESULTS.md) for a
recorded snapshot (headline finding: authorization overhead is ~1.25% of
end-to-end wall clock; bootstrap alone costs ~1.7s warm / ~4.2s cold, paid
once per CLI invocation).

### 1.8 Reading the audit trail directly

Every authorization decision — allowed or denied — is appended as one JSON
line to:

```
<backplane_root>\native_mas_runtime\output\native_mas_audit.jsonl
```

**This file is truncated at the start of every `run_cli.py` /
`run_message()` call** (the engine rebuilds itself fresh each invocation),
so it always holds exactly the trace for the run that just finished. Each
line looks like:

```json
{"event_type": "TOOL_AUTHORIZATION", "decision": "ALLOW_WITH_OBLIGATIONS",
 "allowed": true, "would_block": false, "caller_runtime_id": "knowledge_base_agent",
 "target_runtime_id": "search_faqs", "placement_id": "pep.scanner.scan_edge_017_...",
 "permit_id": "18594dff-...", "policy_version": "a9f375f41a28ee0c",
 "authorization_latency_ms": 9.63, "reason": "VALID_PERMIT_LEASE",
 "trace_id": "trace-native-...", "workflow_instance_id": "workflow-native-...",
 "sequence": 16, "timestamp": "2026-08-26T02:17:06.12Z"}
```

Filter by `trace_id` (printed via `--zt-status`) to isolate one run's story
end to end: `INPUT_AUTHORIZATION` → a sequence of `AGENT_ROUTE_AUTHORIZATION`
/ `TOOL_AUTHORIZATION` events, each with its own decision → `OUTPUT_AUTHORIZATION`.

---

## Part 2 — Architecture and principles, in detail

### 2.1 The one-sentence version

A `google.adk.plugins.base_plugin.BasePlugin` subclass is registered on the
real ADK `App`. ADK calls it at nine defined lifecycle points during every
real invocation; at each one, the plugin asks a real Zero Trust engine
"is this specific call, right now, allowed?" and either lets the real
call through or substitutes a denial in its place — the original agent/tool
code is never modified.

### 2.2 Two repositories, one runtime process

```
Citi_P3\citi_banking_adk\                    Citi_P3_Zero_Trust_Backplane\
(the MAS being protected)                    (the Zero Trust engine)
┌──────────────────────────┐                 ┌──────────────────────────────┐
│ run_cli.py                │                 │ native_mas_runtime/engine.py │
│  -> bank_adk/app.py       │  imports at     │   NativeMasBackplane          │
│     -> build_root_agent() │  runtime via    │     - runs Task 2 scanner    │
│     -> ZeroTrustBackplane │  sys.path       │     - loads/merges ZT-IR     │
│        Plugin(...)  ------│---------------->│     - runs Task 3 placement  │
│  -> bank_adk/runner.py    │                 │     - starts real Task 4 PDP │
│     -> InMemoryRunner     │                 │       (FastAPI, in-process)  │
│        .run_debug(msg)    │                 │     - builds Task 5 Permit   │
│        (REAL LLM here)    │                 │       Lease components       │
└──────────────────────────┘                 └──────────────────────────────┘
```

There is exactly **one Python process**. The MAS repo's `zero_trust/plugin.py`
does `sys.path.insert(0, backplane_root)` and then `from native_mas_runtime
import NativeMasBackplane` — no RPC, no separate service, no serialization
boundary between "the agent framework" and "the security layer." This is a
deliberate simplification for the prototype: the enforcement decision is a
real, signed, policy-driven decision (see 2.5-2.6), but it's reached via an
in-process function call plus an in-process FastAPI `TestClient`, not a
network hop to an external PDP service. (A production deployment would put
the PDP behind a real network boundary; the decision logic itself doesn't
change.)

### 2.3 What gets built once per run: the bootstrap pipeline

Every `NativeMasBackplane(...)` construction (i.e. every `run_cli.py`
invocation) runs this sequence:

```
1. Task 2 scan (subprocess, cached unless --zt-refresh)
   zt_scan.py --repo-root <Citi_P3> --output-dir .../task2/draft_manifests
   -> discovers agents, tools, APIs, resources, interaction edges from
      the ACTUAL Citi_P3 source tree (AST-based static analysis)

2. Task 1 + Task 2 reconciliation
   reconcile_task1_task2_to_unified(...)
   -> maps discovered runtime names (e.g. "fraud_detection_agent") onto
      stable canonical Zero Trust identities (e.g. "agent.front_office.fraud")

3. Merge the scanner graph into the Unified Manifest / ZT-IR
   merge_task2_graph_into_unified(...)

4. Augment with native runtime boundaries (native_mas_runtime-specific)
   _augment_native_boundaries(...)
   -> adds the CLI input channel, the output channel, and the ACTUAL
      instantiated ADK agent tree's edges (runtime_agent_edges, passed in
      from the plugin's own _index_agent_tree() walk of the real root
      agent) as first-class subjects/resources/interactions -- see 2.4

5. Task 3: PEP placement
   build_pep_plan(manifest) -> generate_artifacts(...)
   -> decides, for every interaction edge, whether/what kind of PEP is
      required (agent_route, tool, mcp, api, data, input, output, ...),
      and deduplicates logical PEPs onto shared physical enforcement
      components

6. Task 4: policy compilation + real PDP
   compile_task4(manifest, ...) writes identities.yaml / resources.yaml /
   policy bundles (front_office.yaml, mid_office.yaml, back_office.yaml,
   ledger_access.yaml, tool_access.yaml, native_mas_runtime.yaml, ...)
   build_runtime() + create_app(...) -> a REAL FastAPI PDP app, started
   in-process and health-checked via TestClient (real routing, real
   Pydantic validation, real policy evaluation -- just no real TCP socket)

7. Task 5: Permit Lease components
   build_task5_components(bridge, normal_ttl_seconds=60,
                           critical_ttl_seconds=15, normal_max_uses=3)
```

This whole pipeline is what makes the claim "real Task 2 scanner-discovered
edges are authorizing a live LLM-driven run" literally true, not
aspirational: placements like `pep.scanner.scan_edge_017_knowledge_base_
agent_search_faqs` that show up in a live audit trace are placements Task 3
generated from an edge Task 2's AST scanner actually found in `Citi_P3`'s
source code, in step 1, moments before the LLM call that used it.

### 2.4 Why the runtime agent tree is authoritative, not just the static scan

Static analysis (Task 2) sees source code: constructors, imports, call
sites. It can miss edges that only exist once the program actually runs —
for example, a factory function that builds sub-agents dynamically, or (the
central case here) ADK's own `transfer_to_agent` delegation mechanism, which
isn't a line of `Citi_P3` application code at all; it's `google-adk`
framework plumbing.

So the plugin does its own, independent discovery pass at construction time
— `_index_agent_tree()` walks the **actual, already-instantiated** ADK agent
object graph (`root_agent.sub_agents`, recursively) and records every real
parent→child edge as `runtime_agent_edges`. This list is passed into
`NativeMasBackplane`, and `_augment_native_boundaries()` (step 4 above)
merges it into the manifest as ground truth, synthesizing any subject/
resource/route placement the static scanner didn't already produce.

This is why route-level protection for `user_proxy_agent → front_office_
router → back_office_router → mid_office_router` and every deeper hop
existed and passed the offline wiring probe *before* the `transfer_to_agent`
bug was fixed (see 2.6) — the route PEPs were never the missing piece. What
was missing was routing the right *kind* of check to the right boundary,
which is a plugin-logic question, not a coverage question.

### 2.5 The nine ADK lifecycle hooks, and what each one actually protects

`ZeroTrustBackplanePlugin` implements `google.adk.plugins.base_plugin.
BasePlugin`'s callback surface. ADK calls these at real, defined points in
every real invocation:

| Hook | Fires when | What it checks | On deny |
|---|---|---|---|
| `on_user_message_callback` | The original user message arrives, before ADK does anything with it | `INPUT_AUTHORIZATION` against `pep.native.cli_input` | Records the denial on invocation state (can't return content yet — no run has started) |
| `before_run_callback` | Right after, before the root agent executes | Reads the denial recorded above | Returns a `zero_trust_denied` `Content` object, which ADK uses as the entire response — the root agent never runs |
| `before_agent_callback` | Before **any** agent (root, sub-agent, workflow orchestrator) executes | `AGENT_ROUTE_AUTHORIZATION` for `(parent, this_agent)` via `find_route_placement` | Returns denial `Content`; ADK treats it as that agent's output instead of running it |
| `after_agent_callback` | After an agent finishes | Nothing gated — just appends an `AGENT_EXECUTION_COMPLETE` audit event | — |
| `before_tool_callback` | Before any function-tool actually executes | Special-cases `transfer_to_agent` (→ `_authorize_agent_transfer`, reuses the route logic above); everything else gets `TOOL_AUTHORIZATION` via `find_tool_placement` | Returns a `zero_trust_denied` dict, which ADK feeds back to the LLM as the tool's result — **the real Python function is never called** |
| `after_tool_callback` | After a tool call that was allowed | Lets Task 5 post-process the result (e.g. apply data-masking obligations) | — |
| `on_tool_error_callback` | A tool call raised an exception | Aborts the Task 5 reservation for that call, logs `TOOL_EXECUTION_ERROR` | — |
| `on_event_callback` | Every raw ADK event | Logs `ADK_EVENT` for audit completeness (author, event id, is-final-response) | — |
| `after_run_callback` | The whole invocation is finished | Logs `NATIVE_RUN_COMPLETE`, snapshots trace metadata for `--zt-status` | — |

Plus one method that is **not** an ADK callback (ADK has no "final output"
hook), called explicitly by `bank_adk/runner.py` right after
`runner.run_debug()` returns:

| Method | Called by | What it checks | On deny |
|---|---|---|---|
| `protect_final_output(value)` | `run_message()`, after the ADK run finishes | `OUTPUT_AUTHORIZATION` against `pep.native.cli_output` (hashes the serialized JSON) | Replaces the entire result with a `zero_trust_denied` dict before it's printed/returned |

Nothing in `bank_adk/agents/*.py` or `bank_adk/tools/*.py` was rewritten to
call into the Backplane. The agents and tools are exactly the original
banking application code; enforcement lives entirely in the plugin's
interception of ADK's own lifecycle.

### 2.6 The `transfer_to_agent` lesson (why this matters architecturally, not just historically)

ADK does not expose "agent A hands off to agent B" as a distinct callback.
When an `LlmAgent` has `sub_agents`, ADK gives the LLM a synthetic function
tool literally named `transfer_to_agent(agent_name: str)`; the LLM "calls"
it like any other tool to delegate. That means delegation arrives at
`before_tool_callback`, not some dedicated "before delegation" hook.

The plugin's tool-boundary logic is built to look up a Task-3-generated
**tool PEP** by `(agent, tool_name)` and default-deny if none exists — the
right behavior for a real business tool that was never analyzed and
therefore was never explicitly declared safe. But `transfer_to_agent` is not
a business tool; it is `google-adk` framework plumbing that Task 2's scanner
(which reads `Citi_P3` application source, not ADK's own internals) will
never see. Naively applying the generic rule to it meant **every** real
delegation, on every real message, failed closed before reaching any real
agent or tool — the system was technically wired and technically
fail-closed (arguably "safe"), but functionally protected nothing, because
nothing downstream of the root agent ever ran.

The fix (`_authorize_agent_transfer`, in `before_tool_callback`) special-
cases `transfer_to_agent` and authorizes it using the *same*
`find_route_placement` + `AGENT_ROUTE_AUTHORIZATION` logic `before_
agent_callback` already used successfully — reusing existing, already-
generated route PEPs instead of inventing a new PEP category. `before_
agent_callback`'s own route check is deliberately left in place, unchanged:
some sub-agents (anything invoked from a custom `BaseAgent._run_async_impl`,
like `FrontOfficeTransactionWorkflowAgent`'s deterministic
monitoring→fraud→compliance→decision sequence) are entered by direct
`sub_agent.run_async(ctx)` calls, never through `transfer_to_agent` at all —
so the agent-boundary check is the only thing protecting those hops, and
removing it to avoid "double-checking" the LLM-driven hops would silently
stop protecting the orchestrated ones.

**The general principle:** a framework's own control-flow primitives
(delegation, retries, internal bookkeeping calls) are not automatically
covered by a security layer built around "discover business interactions,
generate PEPs for them" — they have to be explicitly recognized and mapped
to the *correct* enforcement boundary, or a naive default-deny will
either (a) silently block everything, which looks safe but isn't useful, or
in a differently-built system (b) silently allow everything, which is
useful but isn't safe. Fail-closed-and-useless is a much easier bug to ship
and much easier to miss than fail-open, precisely because nothing looks
wrong in isolated component tests — this is exactly why the mocked
`tests_native_zt/` suite alone never caught it, and why
`verify_native_zero_trust_e2e.py` (Part 3) exists.

### 2.7 Enforce mode vs. shadow mode

`--zt-mode enforce` (default): a denial actually stops the action —
`before_agent_callback`/`before_tool_callback` substitute denial content in
place of the real call.

`--zt-mode shadow`: `should_execute()` lets the action proceed even when the
authorization would have denied it (unless it's a critical write and
`--zt-shadow-allow-critical` was not also passed — critical writes still
fail closed even in shadow mode, by default). Every decision is still
computed and audited (`would_block: true/false` in the audit trail), so
shadow mode is how you'd measure "what would Zero Trust have blocked if it
were fully turned on" against real traffic before flipping to enforce.

### 2.8 Task 4: the decision, not just allow/deny

The PDP evaluates `subject + resource + action + context` against versioned
YAML policy bundles (`front_office.yaml`, `mid_office.yaml`,
`back_office.yaml`, `ledger_access.yaml`, `tool_access.yaml`,
`native_mas_runtime.yaml`) and returns one of five decision types:
`ALLOW`, `DENY`, `MANUAL_REVIEW`, `ALLOW_WITH_OBLIGATIONS`,
`REAUTHENTICATE`. Two concrete examples observed in real runs:

- `policy.ledger.eod_pending_review` (priority 300): any EOD ledger update
  matching `agent.back_office.ledger_update` + `resource.back_office.ledger`
  + `update` + `workflow.back_office.eod_processing` is `MANUAL_REVIEW`
  **unless** `policy.ledger.eod_approved_update` (priority 360, higher —
  policies are priority-ordered) also matches, which additionally requires
  `approval_valid: true` and `secondary_authorization_valid: true` in the
  request context — exactly `--zt-human-approval` and
  `--zt-secondary-authorization` — in which case the decision becomes
  `ALLOW_WITH_OBLIGATIONS` with obligations `enhanced_logging`,
  `require_idempotency_key`, `require_change_ticket`,
  `independent_resource_audit`.
- `policy.ledger.report_deny_update` (priority 600, highest): an explicit,
  unconditional `DENY` for the report agent ever writing to the ledger —
  demonstrates that "no policy matched" is not the only source of denial;
  an explicit high-priority deny rule can override everything below it.

### 2.9 Task 5: why a Permit Lease, not a cached boolean

A naive `allow = true` cached after one PDP call is unsafe: it can outlive
the policy, identity, or risk state it was computed against. The Permit
Lease this system issues instead binds: subject, resource, action, **a hash
of the exact request parameters**, workflow id, issue/expiry time, policy
version, identity version, risk version, max uses, and required
obligations, under a real Ed25519 signature. Concretely, in this codebase:

- Normal-risk permits: 60s TTL, up to 3 uses.
- Critical-risk permits: 15s TTL (shorter — a critical write's authorization
  context is more likely to be stale before it's used).
- **Request binding**: authorizing "transfer $50 A→B" produces a lease whose
  hash does not match "transfer $500 A→C" — a modified request cannot reuse
  the old lease; the PDP must be asked again (or, on PDP timeout, the exact
  matching lease is the only thing that can still authorize the retry — see
  the Work Plan's PDP timeout state machine in the top-level Backplane docs).
- Policy or identity version bumps invalidate outstanding leases issued
  under the old version. A PEP restart clears any local cache, so nothing
  survives a restart except what's re-derived from a signed lease that is
  still independently valid.

### 2.10 Fail-closed as the default posture, at multiple independent layers

This system fails closed in several distinct, overlapping ways — worth
naming explicitly because they protect against different failure modes:

1. **No placement found** → `NO_GENERATED_PEP_FOR_NATIVE_PATH`, default
   deny (`engine.authorize`, when `placement is None`). Protects against an
   interaction nobody explicitly analyzed and approved.
2. **PDP unreachable/times out** → high-risk operations deny rather than
   fall back to a stale or absent decision (Task 5's timeout state machine).
3. **`--zero-trust` requires explicit opt-out** (`run_cli.py` default) →
   protects against "someone forgot the flag."
4. **Missing Backplane checkout raises, rather than silently running
   unprotected** (`ZeroTrustBackplanePlugin.__init__`) → protects against a
   broken deployment silently downgrading to no enforcement.
5. **Two independent checks on the same route** (`before_tool_callback`'s
   `transfer_to_agent` special-case *and* `before_agent_callback`'s generic
   route check, see 2.6) → protects against one enforcement point being
   bypassed by a code path that doesn't go through it.

None of these is sufficient alone — (1) is useless if (3)/(4) let you skip
Zero Trust entirely; (5) is why the specific `transfer_to_agent` bug was
fail-closed-but-useless rather than fail-open — but together they're the
actual reason "unauthorized operations are blocked" and "the system doesn't
quietly stop protecting things" hold up as properties of the whole pipeline,
not just of one function.

---

## Part 3 — What "verified" means here, precisely

`verify_native_zero_trust_e2e.py` is not a smoke test that something runs
without crashing. Each of its five scenarios makes an explicit, falsifiable
claim about real behavior, checked against the plugin's own audit trail:

| Scenario | Claim being checked |
|---|---|
| `front_office_faq` | A real, unmodified information request reaches a real tool call and a real answer, with every hop (input, 2 agent routes, tool call, output) individually authorized — nothing silently bypassed, nothing silently denied. |
| `unauthorized_transfer_blocked` | When the real LLM independently decides to attempt a real critical write with no approval context, the real tool function is never invoked — the block happens at the boundary, not in the tool's own logic. |
| `mid_office_representative_assist` | A `ParallelAgent` fan-out (4 concurrent sub-agents) is authorized per-branch, not just at the fan-out's entry point. |
| `back_office_eod_unauthorized` | The same real, multi-step EOD workflow reaches the real critical write and is blocked, proving the block is context-dependent (missing approval), not a permanent deny on that tool. |
| `back_office_eod_authorized` | With human approval + secondary authorization + a change ticket supplied, the identical request is allowed with a real signed permit, and the workflow continues past the write to a second real tool call (`store_report`) — proving the authorization is genuinely contextual and the pipeline doesn't just stop at the first ALLOW. |

Every scenario also runs a shared check —
`no boundary fell through to NO_GENERATED_PEP_FOR_NATIVE_PATH` — a targeted
regression test for the exact class of bug described in 2.6.

Because the real LLM's tool-choice is not deterministic (the same exact
message can, on a different run, ask a clarifying question instead of
calling a tool), checks are split into **preconditions** ("did the LLM
actually make the choice this scenario needs to test anything") and
**security checks** ("was that choice handled correctly"). Only a failed
precondition triggers a retry (up to 3 attempts); a failed security check is
reported immediately, on the first attempt, with no retry — verified by
deliberately reintroducing the `transfer_to_agent` bug and confirming the
harness reported `FAIL` on attempt one, then confirming a clean `PASS` again
after restoring the fix (see the Backplane repo's
`NATIVE_TASK6_INTEGRATION_PROOF.md` for that run's output).

---

## Part 4 — Known limitations (read before citing this as "production-ready")

- Coverage is five hand-picked live scenarios, not exhaustive. Some branches
  (mid-office's planning workflow as opposed to representative-assist; the
  back-office exception path via `EOD-2026-04-21-MISMATCH`) have only the
  offline wiring probe, not a live run.
- `--no-zero-trust` is a real, unauthenticated escape hatch on this CLI. A
  deployment wrapping `run_cli.py` for real use must not expose that flag to
  untrusted callers.
- The PDP here runs in-process via `fastapi.testclient.TestClient`, not
  behind a real network boundary — appropriate for a prototype, not a
  multi-host production topology.
- This is a separate, parallel integration from `integrated_demo`'s
  FastAPI-backend real-write mode (`execute_transfer`/`apply_ledger_updates`
  calling out to `citi_banking_backend` over HTTP). This native path uses
  the local sqlite dev DB in `bank_adk/.runtime/banking.db` instead and does
  not touch that separate backend.
- As of this writing, this entire integration (`bank_adk/zero_trust/`, the
  modified `app.py`/`runner.py`/`run_cli.py`/`agents/front_office.py`, and
  the Backplane's `native_mas_runtime/`) is **uncommitted** in both repos.

---

## Part 5 — File map

```
Citi_P3\citi_banking_adk\
├── NATIVE_ZERO_TRUST_README.md          <- this file
├── run_cli.py                            <- the protected CLI entrypoint
├── verify_native_zero_trust_e2e.py       <- live, automated E2E verification
├── verify_zero_trust_wiring.py           <- fast offline PEP-coverage probe (no LLM call)
├── bank_adk\
│   ├── app.py                            <- builds the real ADK App, attaches the plugin
│   ├── runner.py                         <- run_message(): real InMemoryRunner.run_debug()
│   ├── agents\                           <- UNMODIFIED business logic (except one prompt
│   │                                        tightened in front_office.py, see the proof doc)
│   ├── tools\                            <- UNMODIFIED business tools
│   └── zero_trust\
│       ├── config.py                     <- ZeroTrustConfig (paths, mode, approval flags)
│       └── plugin.py                     <- ZeroTrustBackplanePlugin (Part 2.5-2.6)
└── tests_native_zt\                      <- mocked unit tests of the plugin's own logic

Citi_P3_Zero_Trust_Backplane\
└── native_mas_runtime\
    ├── engine.py                         <- NativeMasBackplane (Part 2.3): bootstrap + all
    │                                        the authorize()/find_*_placement() logic
    ├── README.md
    ├── NATIVE_TASK6_INTEGRATION_PROOF.md <- bug-hunt narrative + live-run evidence
    ├── proof\                            <- snapshotted real runs (JSON + audit traces)
    └── output\                           <- regenerated every run (task2/, task3_generated/,
                                              task4_config/, native_mas_audit.jsonl, ...)
```
