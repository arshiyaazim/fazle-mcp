"""Core-Hermes client for Core's authenticated fixed-query business reader."""
from __future__ import annotations

import os

import httpx

CORE_URL = os.environ.get("FAZLE_CORE_API_URL", "http://127.0.0.1:8200").rstrip("/")
BEARER = os.environ.get("AI_POLICY_RUNNER_BEARER", "")


def query(dataset: str, *, filters=None, search=None, limit=200, offset=0, include_total=True) -> dict:
    if not BEARER:
        return {"error": "Core business read is not configured"}
    body = {
        "dataset": dataset, "filters": filters or {}, "search": search,
        "limit": limit, "offset": offset, "include_total": include_total,
    }
    try:
        response = httpx.post(
            f"{CORE_URL}/api/assistant/ops/business-read/query",
            headers={"Authorization": f"Bearer {BEARER}"}, json=body, timeout=30,
        )
    except httpx.RequestError:
        return {"error": "Core business read is unreachable"}
    if response.status_code in (401, 403):
        return {"error": "Core business read rejected the service credential"}
    if not response.is_success:
        return {"error": f"Core business read failed (status {response.status_code})"}
    try:
        return response.json()
    except ValueError:
        return {"error": "Core business read returned invalid JSON"}


def query_all(dataset: str, *, filters=None, search=None) -> dict:
    """Read all matching rows through Core's verified-Admin endpoint."""
    if not BEARER:
        return {"error": "Core business read is not configured"}
    body = {
        "dataset": dataset,
        "filters": filters or {},
        "search": search,
    }
    try:
        response = httpx.post(
            f"{CORE_URL}/api/assistant/ops/business-read/admin-full-read",
            headers={"Authorization": f"Bearer {BEARER}"},
            json=body, timeout=120,
        )
    except httpx.RequestError:
        return {"error": "Core business read is unreachable"}
    if response.status_code in (401, 403):
        return {"error": "Verified Admin business read rejected"}
    if not response.is_success:
        return {"error": f"Core business read failed (status {response.status_code})"}
    try:
        return response.json()
    except ValueError:
        return {"error": "Core business read returned invalid JSON"}
