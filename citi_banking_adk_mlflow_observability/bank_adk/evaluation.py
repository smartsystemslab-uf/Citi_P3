from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .scenarios import normalize_scenario_name


@dataclass(frozen=True)
class ScenarioExpectation:
    domain: str
    required_tools: tuple[str, ...] = field(default_factory=tuple)
    optional_tools: tuple[str, ...] = field(default_factory=tuple)
    required_result_keys: tuple[str, ...] = field(default_factory=tuple)
    required_transfers: tuple[str, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    expected_decision: str | None = None
    expected_status: str | None = None
    required_text_terms: tuple[str, ...] = field(default_factory=tuple)
    expected_mismatch_amount: str | None = None


SCENARIO_EXPECTATIONS: dict[str, ScenarioExpectation] = {
    "front_office_monitoring": ScenarioExpectation(
        domain="front_office",
        required_tools=(
            "get_customer_context",
            "get_transaction_context",
            "list_recent_transactions",
            "search_policies",
            "submit_manual_review",
        ),
        required_result_keys=("fo_decision_report",),
        required_transfers=("front_office_router", "front_office_transaction_workflow"),
        expected_decision="manual_review",
        required_text_terms=("manual", "review"),
    ),
    "front_office_chatbot": ScenarioExpectation(
        domain="front_office",
        required_tools=("search_faqs",),
        required_result_keys=("fo_knowledge_answer",),
        required_transfers=("front_office_router", "customer_service_chatbot_workflow"),
        required_text_terms=("dispute", "60"),
    ),
    "front_office_transaction_execution": ScenarioExpectation(
        domain="front_office",
        required_tools=("execute_transfer",),
        required_result_keys=("fo_transaction_execution",),
        required_transfers=("front_office_router", "customer_service_chatbot_workflow"),
        expected_status="posted",
        required_text_terms=("125.50", "posted"),
    ),
    "mid_office_planning": ScenarioExpectation(
        domain="mid_office",
        required_tools=(
            "get_operations_snapshot",
            "get_support_playbooks",
            "persist_validated_schedule",
        ),
        required_result_keys=("mid_office_planning_result",),
        required_transfers=("mid_office_router", "mid_office_planning_workflow"),
        required_text_terms=("schedule", "validated"),
    ),
    "mid_office_rep_assist": ScenarioExpectation(
        domain="mid_office",
        required_tools=(
            "get_customer_financial_profile",
            "search_loan_playbooks",
            "search_policies",
        ),
        optional_tools=("search_product_catalog",),
        required_result_keys=("mid_office_representative_result",),
        required_transfers=("mid_office_router", "representative_assistant_workflow"),
        required_text_terms=("loan", "policy", "risk"),
    ),
    "back_office_clean": ScenarioExpectation(
        domain="back_office",
        required_tools=(
            "validate_eod_readiness",
            "get_eod_batch",
            "apply_ledger_updates",
            "get_reconciliation_data",
            "store_report",
        ),
        required_result_keys=("bo_report_output",),
        required_transfers=("back_office_router", "back_office_eod_workflow"),
        forbidden_tools=("create_exception_case",),
        expected_status="generated",
        required_text_terms=("reconciled",),
    ),
    "back_office_mismatch": ScenarioExpectation(
        domain="back_office",
        required_tools=(
            "validate_eod_readiness",
            "get_eod_batch",
            "apply_ledger_updates",
            "get_reconciliation_data",
            "create_exception_case",
        ),
        required_result_keys=("bo_exception_output",),
        required_transfers=("back_office_router", "back_office_eod_workflow"),
        expected_status="open",
        required_text_terms=("mismatch", "exception"),
        expected_mismatch_amount="190.00",
    ),
}


_ACCOUNT_ID_RE = re.compile(r"\b(?:CHK|SAV|ACCT|CARD|LOAN|HELOC)-\d{2,}\b", re.IGNORECASE)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_LARGE_EXACT_MONEY_RE = re.compile(r"\$?\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b")
_JSONISH_RE = re.compile(r"^\s*(?:\{|\[).*(?:\}|\])\s*$", re.DOTALL)
_SAFE_METRIC_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_metric_key(value: str) -> str:
    return _SAFE_METRIC_RE.sub("_", value).strip("._") or "metric"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        return str(value)


def _lower_text(value: Any) -> str:
    return _as_text(value).lower()


def _iter_events(compact_result: Any) -> Iterable[dict[str, Any]]:
    if isinstance(compact_result, list):
        for item in compact_result:
            if isinstance(item, dict):
                yield item


def _events_of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event") == event_type]


def _tool_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        if event.get("event") in {"tool_call", "tool_result"} and event.get("tool"):
            names.append(str(event["tool"]))
    return names


def _transfer_targets(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("to")) for event in events if event.get("event") == "transfer" and event.get("to")]


def _result_by_key(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for event in events:
        if event.get("event") == "result" and event.get("key"):
            out[str(event["key"])] = event.get("value")
    return out


def _final_result_text(events: list[dict[str, Any]]) -> str:
    results = [event.get("value") for event in events if event.get("event") == "result"]
    return "\n".join(_as_text(value) for value in results)


def _count_malformed_json_arguments(events: list[dict[str, Any]]) -> int:
    malformed = 0
    for event in events:
        args = event.get("args")
        if not isinstance(args, dict):
            continue
        for value in args.values():
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not _JSONISH_RE.match(text):
                continue
            try:
                json.loads(text)
            except Exception:
                malformed += 1
    return malformed


def _detect_final_response_pii(final_text: str) -> dict[str, int]:
    return {
        "account_id_leak_detected": int(bool(_ACCOUNT_ID_RE.search(final_text))),
        "ssn_leak_detected": int(bool(_SSN_RE.search(final_text))),
        "email_leak_detected": int(bool(_EMAIL_RE.search(final_text))),
        "phone_leak_detected": int(bool(_PHONE_RE.search(final_text))),
        "large_money_amount_in_final_response": int(bool(_LARGE_EXACT_MONEY_RE.search(final_text))),
    }


def _check_expected_decision(result_values: dict[str, Any], expected_decision: str | None) -> bool:
    if expected_decision is None:
        return True
    for value in result_values.values():
        if isinstance(value, dict) and str(value.get("decision", "")).lower() == expected_decision.lower():
            return True
        if expected_decision.lower() in _lower_text(value):
            return True
    return False


def _check_expected_status(events: list[dict[str, Any]], expected_status: str | None) -> bool:
    if expected_status is None:
        return True
    expected = expected_status.lower()
    for event in events:
        for field in ("result", "value"):
            value = event.get(field)
            if isinstance(value, dict) and str(value.get("status", "")).lower() == expected:
                return True
            if expected in _lower_text(value):
                return True
    return False


def _check_mismatch_amount_consistency(final_text: str, expected_amount: str | None) -> bool:
    if expected_amount is None:
        return True
    normalized = final_text.replace(",", "")
    wrong_values = {"1990.00", "1,990.00", "1990"}
    if any(value.replace(",", "") in normalized for value in wrong_values):
        return False
    return expected_amount.replace(",", "") in normalized


def evaluate_compact_trace(
    scenario_name: str | None,
    compact_result: Any,
    *,
    message: str | None = None,
) -> dict[str, Any]:
    """Rule-based banking workflow evaluator for the compact ADK trace.

    The evaluator intentionally uses deterministic checks, not an LLM judge. That makes
    it stable enough for demos, dashboards, and regression testing.
    """

    scenario = scenario_name or "unknown"
    base_scenario = normalize_scenario_name(scenario)
    expectation = SCENARIO_EXPECTATIONS.get(base_scenario)
    events = list(_iter_events(compact_result))

    transfers = _events_of_type(events, "transfer")
    tool_calls = _events_of_type(events, "tool_call")
    tool_results = _events_of_type(events, "tool_result")
    final_results = _events_of_type(events, "result")

    tools_seen = _tool_names(events)
    tools_seen_set = set(tools_seen)
    transfer_targets = _transfer_targets(events)
    transfer_targets_set = set(transfer_targets)
    result_values = _result_by_key(events)
    final_text = _final_result_text(events)

    required_tools = expectation.required_tools if expectation else tuple()
    optional_tools = expectation.optional_tools if expectation else tuple()
    required_result_keys = expectation.required_result_keys if expectation else tuple()
    required_transfers = expectation.required_transfers if expectation else tuple()
    forbidden_tools = expectation.forbidden_tools if expectation else tuple()
    required_text_terms = expectation.required_text_terms if expectation else tuple()

    missing_tools = sorted(tool for tool in required_tools if tool not in tools_seen_set)
    missing_result_keys = sorted(key for key in required_result_keys if key not in result_values)
    missing_transfers = sorted(target for target in required_transfers if target not in transfer_targets_set)
    forbidden_tools_present = sorted(tool for tool in forbidden_tools if tool in tools_seen_set)
    optional_tools_present = sorted(tool for tool in optional_tools if tool in tools_seen_set)
    missing_text_terms = sorted(term for term in required_text_terms if term.lower() not in final_text.lower())

    malformed_json_args = _count_malformed_json_arguments(events)
    pii_flags = _detect_final_response_pii(final_text)
    # Exact monetary values are not always PII. Transaction execution and
    # back-office reconciliation naturally include amounts. Treat large exact
    # balances as a hard sensitive-data issue only for representative assist,
    # where the final memo should avoid exposing exact balances.
    sensitive_amount_exposure = int(
        base_scenario == "mid_office_rep_assist"
        and pii_flags.get("large_money_amount_in_final_response", 0) == 1
    )

    checks = {
        "expectation_defined": expectation is not None,
        "required_tools_present": not missing_tools,
        "required_result_keys_present": not missing_result_keys,
        "required_transfers_present": not missing_transfers,
        "forbidden_tools_absent": not forbidden_tools_present,
        "required_text_terms_present": not missing_text_terms,
        "expected_decision_matched": _check_expected_decision(result_values, expectation.expected_decision if expectation else None),
        "expected_status_matched": _check_expected_status(events, expectation.expected_status if expectation else None),
        "mismatch_amount_consistent": _check_mismatch_amount_consistency(final_text, expectation.expected_mismatch_amount if expectation else None),
        "tool_arguments_valid_json_when_json_like": malformed_json_args == 0,
        "final_response_no_account_id_leak": pii_flags["account_id_leak_detected"] == 0,
        "final_response_no_ssn_leak": pii_flags["ssn_leak_detected"] == 0,
        "final_response_no_email_leak": pii_flags["email_leak_detected"] == 0,
        "final_response_no_phone_leak": pii_flags["phone_leak_detected"] == 0,
        "final_response_no_sensitive_amount_exposure": sensitive_amount_exposure == 0,
    }

    hard_fail_checks = [
        "expectation_defined",
        "required_tools_present",
        "required_result_keys_present",
        "required_transfers_present",
        "forbidden_tools_absent",
        "required_text_terms_present",
        "expected_decision_matched",
        "expected_status_matched",
        "mismatch_amount_consistent",
        "tool_arguments_valid_json_when_json_like",
        "final_response_no_account_id_leak",
        "final_response_no_ssn_leak",
        "final_response_no_email_leak",
        "final_response_no_phone_leak",
        "final_response_no_sensitive_amount_exposure",
    ]
    overall_pass = all(bool(checks[name]) for name in hard_fail_checks)
    correctness_score = sum(int(bool(checks[name])) for name in hard_fail_checks) / max(len(hard_fail_checks), 1)

    findings: list[str] = []
    if missing_tools:
        findings.append(f"Missing required tools: {', '.join(missing_tools)}")
    if missing_result_keys:
        findings.append(f"Missing required final result keys: {', '.join(missing_result_keys)}")
    if missing_transfers:
        findings.append(f"Missing required agent transfers: {', '.join(missing_transfers)}")
    if forbidden_tools_present:
        findings.append(f"Forbidden tools were used: {', '.join(forbidden_tools_present)}")
    if missing_text_terms:
        findings.append(f"Final response missing expected terms: {', '.join(missing_text_terms)}")
    if malformed_json_args:
        findings.append(f"Detected {malformed_json_args} malformed JSON-like tool argument(s)")
    if not checks["mismatch_amount_consistent"]:
        findings.append("Mismatch amount appears inconsistent; check for 190.00 vs 1990.00 style errors")
    direct_pii_detected = any(
        pii_flags[name]
        for name in (
            "account_id_leak_detected",
            "ssn_leak_detected",
            "email_leak_detected",
            "phone_leak_detected",
        )
    )
    if direct_pii_detected or sensitive_amount_exposure:
        findings.append("Potential sensitive data exposure detected in final response")
    if pii_flags.get("large_money_amount_in_final_response", 0) and not sensitive_amount_exposure:
        findings.append("Large monetary amount present in final response; allowed for this scenario but tracked")

    metrics: dict[str, int | float] = {
        "event_count": len(events),
        "transfer_count": len(transfers),
        "tool_call_count": len(tool_calls),
        "tool_result_count": len(tool_results),
        "final_result_count": len(final_results),
        "unique_agent_count": len({str(event.get("agent")) for event in events if event.get("agent")}),
        "unique_tool_count": len(set(tools_seen)),
        "missing_required_tool_count": len(missing_tools),
        "missing_result_key_count": len(missing_result_keys),
        "missing_transfer_count": len(missing_transfers),
        "forbidden_tool_count": len(forbidden_tools_present),
        "optional_tool_used_count": len(optional_tools_present),
        "malformed_json_argument_count": malformed_json_args,
        "required_tools_present": int(checks["required_tools_present"]),
        "required_result_keys_present": int(checks["required_result_keys_present"]),
        "required_transfers_present": int(checks["required_transfers_present"]),
        "expected_decision_matched": int(checks["expected_decision_matched"]),
        "expected_status_matched": int(checks["expected_status_matched"]),
        "mismatch_amount_consistent": int(checks["mismatch_amount_consistent"]),
        "pii_leak_detected": int(direct_pii_detected or sensitive_amount_exposure),
        "sensitive_amount_exposure_detected": sensitive_amount_exposure,
        "overall_pass": int(overall_pass),
        "correctness_score": round(correctness_score, 4),
        **pii_flags,
    }

    return {
        "scenario_name": scenario,
        "base_scenario": base_scenario,
        "domain": expectation.domain if expectation else "unknown",
        "message_preview": (message or "")[:300],
        "overall_pass": overall_pass,
        "correctness_score": round(correctness_score, 4),
        "metrics": metrics,
        "checks": checks,
        "findings": findings,
        "observed": {
            "tools_seen": sorted(set(tools_seen)),
            "transfers_seen": transfer_targets,
            "result_keys_seen": sorted(result_values.keys()),
            "optional_tools_present": optional_tools_present,
            "missing_tools": missing_tools,
            "missing_result_keys": missing_result_keys,
            "missing_transfers": missing_transfers,
            "forbidden_tools_present": forbidden_tools_present,
            "missing_text_terms": missing_text_terms,
        },
    }


def flatten_metrics(prefix: str, metrics: dict[str, Any]) -> dict[str, float]:
    """Return numeric MLflow-safe metric names."""
    out: dict[str, float] = {}
    safe_prefix = _safe_metric_key(prefix)
    for key, value in metrics.items():
        if isinstance(value, bool):
            out[f"{safe_prefix}.{_safe_metric_key(key)}"] = float(int(value))
        elif isinstance(value, int | float):
            out[f"{safe_prefix}.{_safe_metric_key(key)}"] = float(value)
    return out


def aggregate_evaluations(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    passed = sum(1 for item in evaluations if item.get("overall_pass"))
    scores = [float(item.get("correctness_score", 0.0)) for item in evaluations]

    scenario_rows = []
    for item in evaluations:
        metrics = item.get("metrics", {}) if isinstance(item.get("metrics"), dict) else {}
        scenario_rows.append(
            {
                "scenario_name": item.get("scenario_name"),
                "domain": item.get("domain"),
                "overall_pass": bool(item.get("overall_pass")),
                "correctness_score": item.get("correctness_score"),
                "tool_call_count": metrics.get("tool_call_count"),
                "transfer_count": metrics.get("transfer_count"),
                "pii_leak_detected": metrics.get("pii_leak_detected"),
                "findings": item.get("findings", []),
            }
        )

    return {
        "metrics": {
            "scenario_count": total,
            "passed_scenario_count": passed,
            "failed_scenario_count": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "average_correctness_score": round(sum(scores) / total, 4) if total else 0.0,
        },
        "scenarios": scenario_rows,
    }
