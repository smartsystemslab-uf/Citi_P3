import json
from ..infra.repository import repo

def validate_eod_readiness(batch_id: str) -> str:
    """Check whether a batch is ready for end-of-day processing and return readiness evidence."""
    batch = repo.get_eod_batch(batch_id)
    if not batch:
        return json.dumps({"error": f"batch {batch_id} not found"}, indent=2)
    return json.dumps({"batch_id":batch_id,"ready":bool(batch["ready_flag"]),"status":batch["status"],"business_date":batch["business_date"],"notes":batch["notes_json"]}, indent=2)

def get_eod_batch(batch_id: str) -> str:
    """Return the batch payload and expected totals for EOD processing."""
    return json.dumps(repo.get_eod_batch(batch_id) or {"error": f"batch {batch_id} not found"}, indent=2)

def apply_ledger_updates(batch_id: str, posting_instructions: str) -> str:
    """Apply ledger updates and mark the batch as ledger-updated in the system of record."""
    return json.dumps(repo.apply_ledger_updates(batch_id, posting_instructions), indent=2)

def get_reconciliation_data(batch_id: str) -> str:
    """Return the expected and posted totals used by the reconciliation agent."""
    batch = repo.get_eod_batch(batch_id)
    if not batch:
        return json.dumps({"error": f"batch {batch_id} not found"}, indent=2)
    return json.dumps({"batch_id":batch_id,"expected_total":batch["expected_total"],"ledger_posted_total":batch["ledger_posted_total"],"difference":round(batch["expected_total"]-batch["ledger_posted_total"],2),"status":batch["status"]}, indent=2)

def create_exception_case(batch_id: str, mismatch_summary: str) -> str:
    """Create an exception case for reconciliation mismatches and abnormal EOD outcomes."""
    return json.dumps(repo.create_exception(batch_id, mismatch_summary), indent=2)

def store_report(batch_id: str, report_body: str) -> str:
    """Store a generated end-of-day report after reconciliation succeeds."""
    return json.dumps(repo.save_report(batch_id, report_body), indent=2)
