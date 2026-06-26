"""Thin async client over the RunPod REST API (``rest.runpod.io/v1``).

Deliberately tiny: just the verbs the runner needs (create / get / list /
delete pods). The RunPod ``runpod`` PyPI SDK is sync and its pod coverage is
thin, so we hit REST directly with httpx.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .errors import PreflightError, ProvisionError, RunpodError

DEFAULT_BASE = "https://rest.runpod.io/v1"


def _api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise PreflightError("RUNPOD_API_KEY not set (pass api_key= or export it)")
    return key


class RunpodRest:
    """Async REST client. Use as an async context manager."""

    def __init__(self, api_key: str | None = None, base: str = DEFAULT_BASE, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers={"Authorization": f"Bearer {_api_key(api_key)}"},
            timeout=timeout,
        )

    async def __aenter__(self) -> "RunpodRest":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _err_text(resp: httpx.Response) -> str:
        # RunPod returns errors as [{"error": "..."}] or {"error": "..."}.
        try:
            body = resp.json()
        except Exception:
            return resp.text[:500]
        if isinstance(body, list) and body and isinstance(body[0], dict):
            return body[0].get("error", str(body))
        if isinstance(body, dict):
            return body.get("error", str(body))
        return str(body)

    async def create_pod(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post("/pods", json=body)
        if resp.status_code >= 300:
            raise ProvisionError(f"create_pod failed ({resp.status_code}): {self._err_text(resp)}")
        return resp.json()

    async def get_pod(self, pod_id: str, include_machine: bool = True) -> dict[str, Any]:
        params = {"includeMachine": "true"} if include_machine else None
        resp = await self._client.get(f"/pods/{pod_id}", params=params)
        if resp.status_code >= 300:
            raise RunpodError(f"get_pod failed ({resp.status_code}): {self._err_text(resp)}")
        return resp.json()

    async def list_pods(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/pods")
        if resp.status_code >= 300:
            raise RunpodError(f"list_pods failed ({resp.status_code}): {self._err_text(resp)}")
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    async def delete_pod(self, pod_id: str) -> None:
        resp = await self._client.delete(f"/pods/{pod_id}")
        # 404 = already gone; treat as success (idempotent teardown).
        if resp.status_code >= 300 and resp.status_code != 404:
            raise RunpodError(f"delete_pod failed ({resp.status_code}): {self._err_text(resp)}")
