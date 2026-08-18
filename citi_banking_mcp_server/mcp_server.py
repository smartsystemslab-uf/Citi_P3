import os
import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import json

# Load environment variables (like your core banking API URL)
load_dotenv()

# Define where your core REST API lives (defaulting to localhost for dev)
BACKEND_API_URL = os.getenv("BANKING_BACKEND_URL", "http://localhost:8000").rstrip("/")

# Initialize the MCP Server
# The name "CitiBank-MCP-Service" will be visible to the connecting AI agent
mcp = FastMCP("CitiBank-MCP-Service")
mode = os.getenv("MCP_MODE", "all")

# =========================================================
# Front Office Tools Start
if mode in ["front", "all"]:
    @mcp.tool()
    async def get_transaction(transaction_id: str) -> str:
        """
        Fetches the full details of a transaction from the database.
        Use this first to gather the from_account_id, to_account_id, amount, and currency.
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{BACKEND_API_URL}/transactions/{transaction_id}")
                response.raise_for_status()
                return f"Transaction Details: {response.json()}"
            except httpx.HTTPStatusError as e:
                return f"Error: Transaction not found. Status {e.response.status_code}"

    @mcp.tool()
    async def run_fraud_and_compliance_workflow(from_account_id: str, to_account_id: str, amount: float, currency: str) -> str:
        """
        Runs the internal transaction monitoring and fraud evaluation.
        Requires account IDs and amount (obtain these via get_transaction first).
        Returns a RiskDecision including a risk_score and flagged reasons.
        """
        payload = {
            "from_account_id": from_account_id,
            "to_account_id": to_account_id,
            "amount": amount,
            "currency": currency
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(f"{BACKEND_API_URL}/monitoring/evaluate_transaction", json=payload)
                response.raise_for_status()
                return f"Fraud Evaluation Result: {response.json()}"
            except Exception as e:
                return f"Error running fraud check: {str(e)}"

    @mcp.tool()
    async def update_transaction_status(transaction_id: str, status: str, rationale: str) -> str:
        """
        Updates the transaction status after review.
        Valid statuses are 'APPROVED', 'REJECTED', or 'MANUAL_REVIEW'.
        You must provide your reasoning in the rationale field.
        """
        valid_statuses = ["APPROVED", "REJECTED", "MANUAL_REVIEW"]
        if status not in valid_statuses:
            return f"Error: Status must be one of {valid_statuses}"

        payload = {"status": status, "decision_reason": rationale}
        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(f"{BACKEND_API_URL}/transactions/{transaction_id}/status", json=payload)
                response.raise_for_status()
                return f"Success! Transaction {transaction_id} marked as {status}."
            except Exception as e:
                return f"Error updating status: {str(e)}"
        
# Front Office Tools End
# =========================================================
# Mid Office Tools Start
if mode in ["mid", "all"]:
    @mcp.tool()
    async def get_branch_workload(branch_id: str) -> str:
        """
        Retrieves real-time operational data for a branch, including:
        - Current queue length
        - Average wait time
        - Available tellers vs. total staff
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_API_URL}/mid-office/workload/{branch_id}")
            return response.text

    @mcp.tool()
    async def propose_staffing_adjustment(from_branch: str, to_branch: str, staff_count: int) -> str:
        """
        Simulates the impact of moving staff between branches to reduce wait times.
        Returns a 'Workload Balance Score' (0.0 - 1.0).
        """
        async with httpx.AsyncClient() as client:
            payload = {"from": from_branch, "to": to_branch, "count": staff_count}
            response = await client.post(f"{BACKEND_API_URL}/mid-office/rebalance", json=payload)
            return response.text

    @mcp.tool()
    async def get_customer_product_affinity(customer_id: str) -> str:
        """
        Predictive tool that returns a JSON list of products a customer is likely to need
        based on their spending habits (e.g., 'High-Yield Savings', 'Auto-Loan').
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_API_URL}/mid-office/affinity/{customer_id}")
            return response.text

# Mid Office Tools End
# =========================================================
# Back Office Tools Start
if mode in ["back", "all"]:
    @mcp.tool()
    async def trigger_eod_reconciliation(date: str) -> str:
        """
        Triggers the End-of-Day reconciliation batch process for a specific date (YYYY-MM-DD).
        Returns a summary of the reconciliation run.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{BACKEND_API_URL}/back-office/reconcile", params={"date": date})
            return response.text

    @mcp.tool()
    async def get_reconciliation_mismatches(report_id: str) -> str:
        """
        Retrieves a list of specific ledger mismatches found during a reconciliation run.
        Each mismatch includes the expected vs actual balance and the affected account.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{BACKEND_API_URL}/back-office/mismatches/{report_id}")
            return response.text

    @mcp.tool()
    async def resolve_ledger_entry(transaction_id: str, correction_amount: float, note: str) -> str:
        """
        Adjusts a ledger entry to fix a reconciliation mismatch. 
        Requires a valid transaction_id and the precise adjustment amount.
        """
        async with httpx.AsyncClient() as client:
            payload = {"correction_amount": correction_amount, "note": note}
            response = await client.post(f"{BACKEND_API_URL}/back-office/resolve/{transaction_id}", json=payload)
            return response.text
# =========================================================
# Back Office Tools End

if __name__ == "__main__":
    mcp.run(transport="stdio")
