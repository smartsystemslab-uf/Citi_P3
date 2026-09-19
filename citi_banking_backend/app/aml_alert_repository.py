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