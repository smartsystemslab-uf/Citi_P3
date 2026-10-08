"""Thin HTTP client for calling the citi_banking_backend FastAPI service.

Tools that need backend-computed data (e.g. AML device-sharing hotspots, shared
device/IP adjacency lists, alert feeding) go through here instead of talking to
the backend's database directly, keeping the two services decoupled.
"""

from __future__ import annotations

import httpx

from ..settings import settings


def get(path: str, params: dict | None = None, timeout: float = 5.0) -> dict:
    """Issues a GET request to the backend and returns the parsed JSON body.

    Network/backend failures are caught and returned as an error payload
    rather than raised, so a single unavailable backend call doesn't crash an
    agent workflow built on top of several tools.
    """
    url = f"{settings.backend_base_url.rstrip('/')}{path}"
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "message": str(exc), "data": {}}


def post(path: str, json: dict | None = None, timeout: float = 5.0) -> dict:
    """Issues a POST request to the backend and returns the parsed JSON body."""
    url = f"{settings.backend_base_url.rstrip('/')}{path}"
    try:
        response = httpx.post(url, json=json or {}, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "message": str(exc), "data": {}}
