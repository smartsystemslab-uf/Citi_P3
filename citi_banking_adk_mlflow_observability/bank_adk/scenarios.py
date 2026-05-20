from __future__ import annotations

from typing import Optional

SCENARIOS: dict[str, str] = {
    "front_office_monitoring": "Review transaction TXN-1001 for customer CUST-001. Run the front-office transaction monitoring, fraud, and compliance workflow and decide whether to approve or send to manual review.",
    "front_office_chatbot": "Customer asks: How do I dispute a debit card transaction and what is the expected timeline?",
    "front_office_transaction_execution": "Customer service request: Transfer 125.50 USD from source account CHK-002 to destination account EXT-998 for customer CUST-002 with memo Utility backup payment.",
    "mid_office_planning": "Use the mid-office planning workflow to analyze operations data for 2026-04-21, forecast workload, propose staffing, validate it, and provide support guidance.",
    "mid_office_rep_assist": "Representative assist request for customer CUST-001. Provide customer data, a product suggestion for idle cash, loan guidance for a home equity inquiry, and policy/risk checks.",
    "back_office_clean": "Core banking system event: Run end-of-day processing for batch EOD-2026-04-21-CLEAN and complete reconciliation and reporting.",
    "back_office_mismatch": "Core banking system event: Run end-of-day processing for batch EOD-2026-04-21-MISMATCH and handle any reconciliation mismatch according to the workflow.",
}


def normalize_scenario_name(name: Optional[str]) -> str:
    """Map a dataset case id back to one of the base workflow scenario names.

    Dataset case ids are intentionally unique, for example:
        front_office_monitoring__failure__tool_omission__r001

    The evaluator and reporting layer still need to know the underlying base workflow.
    """
    if not name:
        return "unknown"
    if name in SCENARIOS:
        return name
    for base_name in SCENARIOS:
        if name.startswith(base_name + "__") or base_name in name:
            return base_name
    return name
