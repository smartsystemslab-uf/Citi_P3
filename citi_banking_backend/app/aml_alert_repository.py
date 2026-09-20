import os
import sqlite3
from pathlib import Path


DEFAULT_AML_DB_PATH = (
    Path(__file__).resolve().parents[2]
    / "citi_banking_adk"
    / "bank_adk"
    / ".runtime"
    / "banking.db"
)

AML_DB_PATH = Path(
    os.getenv("AML_DB_PATH", str(DEFAULT_AML_DB_PATH))
)

ALLOWED_STATUS_TRANSITIONS = {
    "open": {"in-investigation"},
    "in-investigation": {"cleared", "escalated-to-sar"},
    "cleared": set(),
    "escalated-to-sar": set(),
}


class AMLAlertNotFoundError(Exception):
    pass


class InvalidAMLAlertTransitionError(Exception):
    pass

def list_aml_alerts(
    customer_id: str | None = None,
    status: str | None = None,
    alert_type: str | None = None,
) -> list[dict]:
    query = "SELECT * FROM aml_alerts"
    conditions = []
    params = []

    if customer_id:
        conditions.append("customer_id = ?")
        params.append(customer_id)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if alert_type:
        conditions.append("alert_type = ?")
        params.append(alert_type)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC"

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]

def transition_aml_alert_status(
    alert_id: str,
    new_status: str,
) -> dict:
    new_status = new_status.strip().lower()

    with sqlite3.connect(AML_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        alert = conn.execute(
            "SELECT * FROM aml_alerts WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()

        if alert is None:
            raise AMLAlertNotFoundError(
                f"AML alert {alert_id} not found"
            )

        current_status = alert["status"].strip().lower()

        allowed_next_statuses = ALLOWED_STATUS_TRANSITIONS.get(
            current_status,
            set(),
        )

        if new_status not in allowed_next_statuses:
            raise InvalidAMLAlertTransitionError(
                f"Invalid AML alert status transition: "
                f"{current_status} -> {new_status}"
            )

        conn.execute(
            "UPDATE aml_alerts SET status = ? WHERE alert_id = ?",
            (new_status, alert_id),
        )

        updated = conn.execute(
            "SELECT * FROM aml_alerts WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()

    return dict(updated)