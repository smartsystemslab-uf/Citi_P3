from __future__ import annotations

import json
from typing import Any

import httpx

from ..settings import settings


class BankingApiError(RuntimeError):
    pass


class BankingApiClient:
    """Small HTTP client used by ADK tools to call the external Banking API backend."""

    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = (base_url or settings.banking_backend_base_url).rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {}
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json()
            except Exception:
                detail = exc.response.text
            raise BankingApiError(f"Banking API returned {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise BankingApiError(f"Cannot reach Banking API at {self.base_url}: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def get_customer_context(self, customer_id: str) -> dict[str, Any]:
        return self._request("GET", f"/customers/{customer_id}/context")

    def get_customer(self, customer_id: str) -> dict[str, Any]:
        return self._request("GET", f"/customers/{customer_id}")

    def list_customer_accounts(self, customer_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/customers/{customer_id}/accounts")

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        return self._request("GET", f"/transactions/{transaction_id}")

    def list_recent_transactions(self, customer_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._request("GET", f"/customers/{customer_id}/transactions", params={"limit": limit})

    def search_policies(self, query: str) -> list[dict[str, Any]]:
        return self._request("GET", "/policies/search", params={"q": query})

    def list_rules(self, category: str | None = None) -> list[dict[str, Any]]:
        params = {"category": category} if category else None
        return self._request("GET", "/rules", params=params)

    def evaluate_transaction(self, from_account_id: str, to_account_id: str, amount: float, currency: str = "USD") -> dict[str, Any]:
        return self._request(
            "POST",
            "/monitoring/evaluate_transaction",
            json={
                "from_account_id": from_account_id,
                "to_account_id": to_account_id,
                "amount": amount,
                "currency": currency,
            },
        )

    def execute_transfer(
        self,
        source_account: str,
        destination_account: str,
        amount: float,
        memo: str = "",
        customer_id: str | None = None,
        actor: str = "transaction_processing_agent",
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            "/banking-api/transfer",
            json={
                "from_account_id": source_account,
                "to_account_id": destination_account,
                "amount": amount,
                "currency": "USD",
                "actor": actor,
                "memo": memo,
                "customer_id": customer_id,
                "execute_if_low_risk": True,
            },
        )
        # Keep the shape close to the old local repository tool output.
        return {
            "transaction_id": result.get("transaction_id"),
            "status": result.get("status"),
            "source_account": result.get("from_account_id"),
            "destination_account": result.get("to_account_id"),
            "amount": result.get("amount"),
            "currency": result.get("currency", "USD"),
            "risk_score": result.get("risk_score"),
            "decision_reason": result.get("decision_reason"),
            "memo": memo,
            "customer_id": customer_id,
            "raw_backend_result": result,
        }


api_client = BankingApiClient()


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
