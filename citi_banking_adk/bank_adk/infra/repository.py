from __future__ import annotations
import json, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from ..settings import settings

class BankingRepository:
    def __init__(self, db_path=None) -> None:
        self.db_path = db_path or settings.sqlite_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            full_name TEXT, risk_tier TEXT, kyc_status TEXT,
            occupation TEXT, home_country TEXT, notes_json TEXT
        );
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            customer_id TEXT, account_type TEXT, currency TEXT,
            balance REAL, status TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            customer_id TEXT, source_account TEXT, destination_account TEXT,
            amount REAL, currency TEXT, channel TEXT, txn_type TEXT,
            merchant_or_counterparty TEXT, status TEXT, metadata_json TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS manual_reviews (
            case_id TEXT PRIMARY KEY, case_type TEXT, payload TEXT, created_at TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS saved_schedules (
            schedule_id TEXT PRIMARY KEY, schedule_json TEXT, created_at TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS eod_batches (
            batch_id TEXT PRIMARY KEY, business_date TEXT, expected_total REAL,
            ledger_posted_total REAL, ready_flag INTEGER, status TEXT, notes_json TEXT
        );
        CREATE TABLE IF NOT EXISTS exceptions (
            exception_id TEXT PRIMARY KEY, batch_id TEXT, mismatch_summary TEXT, created_at TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY, batch_id TEXT, report_body TEXT, created_at TEXT, status TEXT
        );
        """
        with self.connect() as conn:
            conn.executescript(schema)
            count = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
            if count == 0:
                self._seed(conn)

    def _seed(self, conn):
        customers = [
            ("CUST-001", "Ava Patel", "medium", "verified", "Physician", "US", json.dumps({"typical_txn_band":"50-3500","usual_channels":["mobile_app","web"],"known_payees":["Landlord Co","North Star Brokerage"],"behavior_notes":["Rarely sends wires above 5000 USD","Usually transacts from Florida or New York"]})),
            ("CUST-002", "Daniel Chen", "low", "verified", "Software Engineer", "US", json.dumps({"typical_txn_band":"10-1200","usual_channels":["mobile_app"],"known_payees":["Utility Hub","City Rent"],"behavior_notes":["Pays bills on weekdays","No prior compliance flags"]})),
        ]
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", customers)
        accounts = [
            ("CHK-001", "CUST-001", "checking", "USD", 41850.37, "active"),
            ("SVG-001", "CUST-001", "savings", "USD", 128500.00, "active"),
            ("CHK-002", "CUST-002", "checking", "USD", 9620.13, "active"),
            ("EXT-998", "CUST-999", "external", "USD", 0.0, "external"),
        ]
        conn.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)", accounts)
        now = datetime.now(timezone.utc).isoformat()
        txns = [
            ("TXN-1001", "CUST-001", "CHK-001", "EXT-998", 9200.00, "USD", "mobile_app", "wire_transfer", "New Counterparty Alpha Exports", "pending_review", json.dumps({"geo":"Romania","device_change":True,"velocity_24h":3,"note":"Customer initiated after midnight local time"}), now),
            ("TXN-1002", "CUST-002", "CHK-002", "EXT-998", 220.55, "USD", "mobile_app", "bill_payment", "Utility Hub", "posted", json.dumps({"geo":"Florida","device_change":False,"velocity_24h":1,"note":"Routine payment"}), now),
        ]
        conn.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", txns)
        batches = [
            ("EOD-2026-04-21-CLEAN", "2026-04-21", 18250.55, 18250.55, 1, "ready", json.dumps({"expected_items":43,"source_system":"core_banking_system"})),
            ("EOD-2026-04-21-MISMATCH", "2026-04-21", 18310.55, 18120.55, 1, "ready", json.dumps({"expected_items":43,"source_system":"core_banking_system"})),
        ]
        conn.executemany("INSERT INTO eod_batches VALUES (?, ?, ?, ?, ?, ?, ?)", batches)

    def _one(self, q, *params):
        with self.connect() as conn:
            row = conn.execute(q, params).fetchone()
            return dict(row) if row else None

    def _all(self, q, *params):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def get_customer(self, customer_id: str):
        row = self._one("SELECT * FROM customers WHERE customer_id = ?", customer_id)
        if row:
            row["notes_json"] = json.loads(row["notes_json"])
            row["accounts"] = self._all("SELECT account_id, account_type, currency, balance, status FROM accounts WHERE customer_id = ?", customer_id)
        return row

    def get_transaction(self, transaction_id: str):
        row = self._one("SELECT * FROM transactions WHERE transaction_id = ?", transaction_id)
        if row:
            row["metadata_json"] = json.loads(row["metadata_json"])
        return row

    def list_recent_transactions(self, customer_id: str, limit: int = 5):
        return self._all("SELECT transaction_id, amount, currency, channel, txn_type, merchant_or_counterparty, status, created_at FROM transactions WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?", customer_id, limit)

    def execute_transfer(self, source_account: str, destination_account: str, amount: float, memo: str, customer_id: str | None = None):
        with self.connect() as conn:
            source = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (source_account,)).fetchone()
            if not source:
                raise ValueError(f"Unknown source account: {source_account}")
            if source["balance"] < amount:
                raise ValueError("Insufficient funds for requested transfer")
            dest = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (destination_account,)).fetchone()
            if not dest:
                conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)", (destination_account, "EXTERNAL", "external", source["currency"], 0.0, "external"))
            conn.execute("UPDATE accounts SET balance = balance - ? WHERE account_id = ?", (amount, source_account))
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?", (amount, destination_account))
            txn_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (txn_id, customer_id or source["customer_id"], source_account, destination_account, amount, source["currency"], "banking_api", "transfer", destination_account, "posted", json.dumps({"memo":memo,"executed_by":"transaction_processing_agent"}), now))
            source_after = conn.execute("SELECT balance FROM accounts WHERE account_id = ?", (source_account,)).fetchone()["balance"]
            dest_after = conn.execute("SELECT balance FROM accounts WHERE account_id = ?", (destination_account,)).fetchone()["balance"]
            return {"transaction_id":txn_id,"status":"posted","source_account":source_account,"destination_account":destination_account,"amount":amount,"source_balance_after":source_after,"destination_balance_after":dest_after,"memo":memo,"timestamp":now}

    def create_manual_review(self, case_type: str, payload: str):
        case_id = f"MR-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO manual_reviews VALUES (?, ?, ?, ?, ?)", (case_id, case_type, payload, now, "open"))
        return {"case_id":case_id,"case_type":case_type,"status":"open","created_at":now}

    def save_schedule(self, schedule_json: str):
        sid = f"SCH-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO saved_schedules VALUES (?, ?, ?, ?)", (sid, schedule_json, now, "validated"))
        return {"schedule_id":sid,"status":"validated","created_at":now}

    def get_eod_batch(self, batch_id: str):
        row = self._one("SELECT * FROM eod_batches WHERE batch_id = ?", batch_id)
        if row:
            row["notes_json"] = json.loads(row["notes_json"])
        return row

    def apply_ledger_updates(self, batch_id: str, posting_instructions: str):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            batch = conn.execute("SELECT * FROM eod_batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if not batch:
                raise ValueError(f"Unknown batch_id: {batch_id}")
            conn.execute("UPDATE eod_batches SET status = ? WHERE batch_id = ?", ("ledger_updated", batch_id))
        return {"batch_id":batch_id,"status":"ledger_updated","posting_instructions":posting_instructions,"timestamp":now}

    def create_exception(self, batch_id: str, mismatch_summary: str):
        eid = f"EX-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO exceptions VALUES (?, ?, ?, ?, ?)", (eid, batch_id, mismatch_summary, now, "open"))
            conn.execute("UPDATE eod_batches SET status = ? WHERE batch_id = ?", ("exception", batch_id))
        return {"exception_id":eid,"batch_id":batch_id,"status":"open","created_at":now}

    def save_report(self, batch_id: str, report_body: str):
        rid = f"RPT-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?)", (rid, batch_id, report_body, now, "generated"))
            conn.execute("UPDATE eod_batches SET status = ? WHERE batch_id = ?", ("reported", batch_id))
        return {"report_id":rid,"batch_id":batch_id,"status":"generated","created_at":now}

repo = BankingRepository()
