# Changelog — Native Zero Trust Integration

What changed in this repo (`Citi_P3/citi_banking_adk`), specifically, during
the session that debugged, fixed, and verified the native Zero Trust
integration. This document exists to answer one question precisely: *of
everything currently in `git log` for this integration, which parts were
already here, and which parts were added or changed during that session.*

Both commits below (`44203899`, `24043fd1`) were made by the repo owner
after the session ended — nothing here was committed automatically.

---

## 0. Baseline: what already existed before this session touched anything

The last real commit before any of this was `1d5958ad` ("new API key
updated"). Sitting on top of it, **uncommitted**, was already a first draft
of a native ADK Zero Trust plugin — built earlier, by someone/something
outside this session's visibility, not by the work described below:

- `bank_adk/zero_trust/plugin.py` (376 lines) — a `ZeroTrustBackplanePlugin`
  registered on the real `google.adk` `App`, with all nine lifecycle
  callbacks already wired.
- `bank_adk/zero_trust/config.py`, `__init__.py`.
- `bank_adk/app.py`, `bank_adk/runner.py` — already modified to build the
  plugin and thread `zero_trust_config` through `run_message()`.
- `run_cli.py` — already had `--zero-trust`, `--zt-human-approval`,
  `--zt-secondary-authorization`, `--zt-change-ticket`, etc.
- `tests_native_zt/test_native_runner_wiring.py`,
  `tests_native_zt/test_native_zero_trust_plugin.py` — mocked unit tests of
  the plugin's callback logic.
- `verify_zero_trust_wiring.py` — an offline (no LLM call) PEP-coverage probe.

This draft **ran**, but had four real problems, found by tracing its one
existing live audit log rather than by reading the code:

1. Every real multi-agent handoff failed closed
   (`NO_GENERATED_PEP_FOR_NATIVE_PATH` on `transfer_to_agent`) — no request
   ever reached a real sub-agent or a real tool.
2. `--zero-trust` was opt-in; running the CLI normally enforced nothing.
3. Every real run crashed with a Windows access violation (segfault, exit
   139) during interpreter shutdown, after printing the correct result.
4. No automated way to verify any of this end to end, live, without manual
   `run_cli.py` invocations.

Everything below is what closed those four problems, plus the fixed and
verified proof of the outcome.

---

## 1. Fixed: `bank_adk/zero_trust/plugin.py`

**Problem:** ADK exposes LLM-driven agent delegation
(`user_proxy_agent → front/mid/back_office_router`, `router → workflow`) as
a synthetic function-tool call named `transfer_to_agent(agent_name: str)`,
not as a distinct callback. `before_tool_callback` treated it like any real
business tool, looked up a Task-3-generated tool PEP for it, found none
(Task 2's scanner reads `Citi_P3` application source, not ADK's own
internals), and hit the generic default-deny. Result: every real delegation,
on every real message, failed closed before reaching any real agent or tool
— technically fail-closed, but functionally protected nothing, since
nothing downstream of the root agent ever ran.

**Fix:**

```diff
+_AGENT_TRANSFER_TOOL_NAMES = frozenset({"transfer_to_agent"})
```

```diff
+    def _authorize_agent_transfer(self, *, agent_name, tool_args, state):
+        """Authorize a transfer_to_agent handoff using the same
+        find_route_placement + AGENT_ROUTE_AUTHORIZATION logic
+        before_agent_callback already uses, instead of the generic
+        tool-boundary default-deny."""
+        target_agent = str(tool_args.get("agent_name", "") or "")
+        placement = self.engine.find_route_placement(agent_name, target_agent)
+        ...
```

```diff
     async def before_tool_callback(self, *, tool, tool_args, tool_context):
         ...
+        if tool_name in _AGENT_TRANSFER_TOOL_NAMES:
+            return self._authorize_agent_transfer(
+                agent_name=agent_name, tool_args=tool_args, state=state
+            )
         workflow_id = self._workflow_for(agent_name)
         placement = self.engine.find_tool_placement(...)
```

`before_agent_callback`'s own route check was deliberately left unchanged:
some sub-agents (e.g. `FrontOfficeTransactionWorkflowAgent`'s deterministic
monitoring→fraud→compliance→decision sequence) are entered by direct
`sub_agent.run_async(ctx)` calls, never through `transfer_to_agent` — the
agent-boundary check is the only thing protecting those hops.

Also improved the constructor's `FileNotFoundError` message (raised when the
Backplane checkout can't be found) to point at `--zt-backplane-root` or
`--no-zero-trust`, so a missing Backplane checkout never gets "solved" by
silently downgrading to unprotected.

Net: ~60 lines added to an existing 376-line file.

---

## 2. Fixed: `run_cli.py` — segfault and insecure default

**Segfault, root cause:** reproduced with `PYTHONFAULTHANDLER=1` —
`Windows fatal exception: access violation` inside
`asyncio.proactor_events.py`'s pipe-transport `__del__`/`__repr__`, run
during interpreter-shutdown garbage collection after the transport's
handle is already gone. A known CPython-on-Windows `ProactorEventLoop`
failure mode. `run_cli.py` never spawns an MCP subprocess (only
`run_scenarios.py` does), so switching just this entrypoint to the Selector
loop is safe:

```diff
+if sys.platform == "win32":
+    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

Verified: every real run since exits `0` cleanly.

**Insecure default:** `--zero-trust` was opt-in — a forgotten flag meant a
real message ran with zero enforcement.

```diff
-ap.add_argument("--zero-trust", action="store_true", help="Enable native Zero Trust Backplane enforcement.")
+ap.add_argument("--zero-trust", dest="zero_trust", action="store_true", default=True,
+                help="Enable native Zero Trust Backplane enforcement (default: on).")
+ap.add_argument("--no-zero-trust", dest="zero_trust", action="store_false",
+                help="Disable native Zero Trust Backplane enforcement. Requires explicit "
+                "opt-out; NOT recommended outside local debugging/comparison runs.")
```

Verified: bare `run_cli.py --message "hi" --zt-status` now shows an active
`zero_trust` audit block with no flag at all; `--no-zero-trust` genuinely
produces a plain, un-audited result.

Net: ~28 lines added to an existing ~89-line file.

---

## 3. Fixed: `bank_adk/agents/front_office.py`

**Problem, found live:** in a real run where a critical transfer was
correctly blocked by Zero Trust, the `transaction_processing_agent`'s own
JSON summary still reported `"execution_performed": true` — the security
boundary held (the real tool never ran), but the agent's narrative was
false and potentially misleading to a customer.

**Fix** (the entire diff — this file was otherwise untouched):

```diff
-instruction="... Return a compact JSON string with keys status, execution_performed,
-transaction_id, outcome, and customer_message. If execution is not required or the
-fields are incomplete, return a compact JSON string explaining that no backend
-posting was performed. When calling a tool, use the exact tool name only."
+instruction="... Return a compact JSON string with keys status, execution_performed,
+transaction_id, outcome, and customer_message. If execution is not required or the
+fields are incomplete, return a compact JSON string explaining that no backend
+posting was performed. If the execute_transfer tool result has status
+\"zero_trust_denied\", the transfer was blocked before any funds moved: set
+execution_performed to false, transaction_id to an empty string, outcome to the
+denial reason, and customer_message to a truthful statement that the transfer was
+not completed and requires manual review. Never report execution_performed as true
+unless the tool result confirms the transfer actually posted. When calling a tool,
+use the exact tool name only."
```

Verified live: re-running the same blocked-transfer scenario after this fix
produced `"execution_performed": false, "outcome": "TASK4_MANUAL_REVIEW"`.

---

## 4. Added: regression test in `tests_native_zt/test_native_zero_trust_plugin.py`

New test function `test_transfer_to_agent_is_authorized_as_agent_route_not_
generic_tool` (~38 lines added to an existing 361-line file): asserts
`before_tool_callback` on tool name `transfer_to_agent` calls
`find_route_placement`, evaluates as `AGENT_ROUTE_AUTHORIZATION` (not
`begin_tool`), and correctly denies when the route would be denied. This is
the fast, mocked regression guard for the bug in §1; full suite is
10/10 passing.

---

## 5. New file: `verify_native_zero_trust_e2e.py` (464 lines)

A live (non-mocked) automated verification harness — see
[`NATIVE_ZERO_TRUST_README.md`](NATIVE_ZERO_TRUST_README.md) Part 3 for full
detail. Five real scenarios (authorized front-office read, unauthorized
front-office transfer blocked, mid-office parallel fan-out, back-office
critical write blocked without approval, same write allowed with approval),
each graded against the plugin's own audit trail rather than the LLM's
non-deterministic wording. Includes a targeted regression check for the §1
bug (`no boundary fell through to NO_GENERATED_PEP_FOR_NATIVE_PATH`) and a
precondition/security-check split so LLM tool-choice variance triggers a
retry while a genuine security failure never does. Verified against a
deliberate negative control: reintroducing the §1 bug made this harness
report `FAIL` on the first attempt with the exact right diagnostic; the fix
restored it to a clean `PASS`.

---

## 6. New file: `NATIVE_ZERO_TRUST_README.md` (547 lines)

How to run the native integration, and a detailed architecture walkthrough:
the two-repo/one-process design, the Task 1→5 bootstrap pipeline that runs
on every invocation, why the instantiated ADK agent tree (not just the
static scanner) is authoritative for routing, all nine ADK lifecycle hooks
and what each protects, the `transfer_to_agent` lesson generalized, enforce
vs. shadow mode, Task 4's decision types with real policy examples, why a
signed Permit Lease replaces a cached boolean, and the five independent
fail-closed layers.

---

## 7. Side effects (not code changes — noted so they aren't mistaken for one)

- `bank_adk/.runtime/banking.db` changed (binary diff) because the live
  verification scenarios really executed the EOD ledger-update workflow
  against the local sqlite dev database. Expected, not a bug.
- The two commits' raw diff stat (`44720 insertions`) is dominated by
  `.venv/Lib/site-packages/{_pytest,pluggy,iniconfig,pytest,pytest_asyncio}/*`
  and `citi_banking_adk.egg-info/*` — installing `pytest`/`pytest-asyncio`
  into this repo's venv (needed to run `tests_native_zt/`) got swept into
  the commit because this repo's `.venv` is tracked in git. None of that is
  an intentional change to the MAS; the real functional diff across every
  file in §1-§6 combined is under 200 lines.

---

## 8. Files touched vs. not touched, at a glance

| File | Status |
|---|---|
| `bank_adk/zero_trust/plugin.py` | Pre-existing, **fixed** (§1) |
| `run_cli.py` | Pre-existing, **fixed** (§2) |
| `bank_adk/agents/front_office.py` | Pre-existing, **fixed** (§3) |
| `tests_native_zt/test_native_zero_trust_plugin.py` | Pre-existing, **test added** (§4) |
| `verify_native_zero_trust_e2e.py` | **New** (§5) |
| `NATIVE_ZERO_TRUST_README.md` | **New** (§6) |
| `bank_adk/app.py` | Pre-existing, not touched |
| `bank_adk/runner.py` | Pre-existing, not touched |
| `bank_adk/zero_trust/config.py`, `__init__.py` | Pre-existing, not touched |
| `tests_native_zt/test_native_runner_wiring.py` | Pre-existing, not touched |
| `verify_zero_trust_wiring.py` | Pre-existing, not touched |
