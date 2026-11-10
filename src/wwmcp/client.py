"""Thin HTTP client for wwApi /v1. Never raises on an HTTP error status: the
orchestrator acts on wwApi's own error body (message, per-index issues), so
every call returns a plain dict with `ok` set."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Profile

DEFAULT_TIMEOUT = 30.0
LLM_TIMEOUT = 180.0  # the server-draft endpoints wait on a model


class WwApi:
    def __init__(self, profile: Profile, transport: httpx.BaseTransport | None = None):
        self.profile = profile
        self._http = httpx.Client(
            base_url=profile.base_url,
            headers={"X-Api-Key": profile.api_key},
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            r = self._http.request(
                method, path, params=params or None, json=json,
                timeout=timeout or DEFAULT_TIMEOUT,
            )
        except httpx.HTTPError as e:
            return {
                "ok": False,
                "http_status": None,
                "error": f"could not reach {self.profile.base_url}: {e.__class__.__name__}: {e}",
            }
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:2000]}
        # `http_status`, not `status`: wwApi bodies carry their own `status`
        # (draft/approved/...) and the two must never be confused.
        if r.is_success:
            out = dict(body) if isinstance(body, dict) else {"data": body}
            out["ok"] = True
            out["http_status"] = r.status_code
            return out
        # wwApi errors: {"error": {"status", "message", "code"?}, "issues"?: [...]}
        err = body.get("error") if isinstance(body, dict) else None
        out = {"ok": False, "http_status": r.status_code}
        if isinstance(err, dict):
            out["error"] = err.get("message") or str(err)
            if err.get("code"):
                out["code"] = err["code"]
        else:
            out["error"] = str(err or body)
        if isinstance(body, dict) and body.get("issues") is not None:
            out["issues"] = body["issues"]
        return out

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any = None, **kw: Any) -> dict[str, Any]:
        return self.request("POST", path, json=json, **kw)

    def patch(self, path: str, json: Any = None) -> dict[str, Any]:
        return self.request("PATCH", path, json=json)

    def delete(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("DELETE", path, params=params)
