"""HTTP adapter: the provider is a service that speaks a small JSON contract.

config:
  base_url   - where the provider lives
  invoke_path (default "/invoke"), health_path (default "/health")
  auth       - {"type": "bearer", "secret": "<secret name>"}
               or {"type": "header", "name": "X-API-Key", "secret": "<secret name>"}
               or absent

The Bevro contract (default):

POST {base_url}{invoke_path}  {"task_id", "run_id", "request", "input"}
  -> {"state": "completed"|"failed"|"needs_input"|"needs_approval"|"running",
      "summary": "...", "error": "...", "external_ref": "...",
      "artifacts": [{"type","title","summary","mime_type","payload","external_url","metadata"}]}

An existing service that speaks its own JSON (found through OpenAPI, or set
up by hand) gets an *adapter profile* instead, kept on Bevro's side:

  invoke   - {"method": "POST", "path": "/ask", "body": {"query": "{request}"}}
             every string in `body` may contain {request}; nothing else is
             interpolated. GET puts `body` into the query string.
  response - {"summary": "answer", "text": "answer"}: dotted paths into the
             JSON reply for the one-line outcome and the full text. Absent:
             the reply is shown as it came.

The service itself is never changed; Bevro adapts to it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from adapters.base import (
    ArtifactDraft,
    FailureKind,
    HealthResult,
    InvocationContext,
    InvocationRequest,
    InvocationResult,
    NotSupported,
    ProviderSpec,
    ResultState,
)
from adapters.registry import register_adapter
from adapters.runtime import BaseRuntimeAdapter
from adapters import urlsafety

TIMEOUT = 30.0
log = logging.getLogger("bevro.adapters.http")


def _headers(config: dict[str, Any], secrets: dict[str, str]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    auth = config.get("auth") or {}
    token = secrets.get(auth.get("secret", "api_key")) if auth else None
    if token and auth.get("type") == "bearer":
        headers["Authorization"] = f"Bearer {token}"
    elif token and auth.get("type") == "header" and auth.get("name"):
        headers[str(auth["name"])] = token
    return headers


def _fill(template: Any, request_text: str) -> Any:
    """Replace {request} inside strings of a JSON template. Nothing else is touched."""
    if isinstance(template, str):
        return template.replace("{request}", request_text)
    if isinstance(template, dict):
        return {k: _fill(v, request_text) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, request_text) for v in template]
    return template


def _pick(data: Any, path: str | None) -> Any:
    """Follow a dotted path ("result.answer", "items.0.text") into JSON."""
    if not path:
        return None
    node = data
    for part in str(path).split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _first_line(text: str, limit: int = 200) -> str | None:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "").lstrip("#*- ").strip()
    if not line:
        return None
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "…"


def _parse_profiled(data: Any, mapping: dict[str, Any], provider_name: str) -> InvocationResult:
    """Interpret a reply from a service with its own JSON shape."""
    text = _as_text(_pick(data, mapping.get("text")) if mapping.get("text") else data)
    summary = _pick(data, mapping.get("summary")) if mapping.get("summary") else None
    summary_text = _first_line(_as_text(summary)) if summary is not None else _first_line(text)
    artifacts: list[ArtifactDraft] = []
    if text:
        artifacts.append(ArtifactDraft(type="report", title=f"Answer from {provider_name}", mime_type="text/markdown", payload={"text": text[:100_000]}))
    if isinstance(data, (dict, list)) and not mapping.get("text"):
        artifacts.append(ArtifactDraft(type="structured", title="Details", payload=data))
    link = _pick(data, mapping.get("link")) if mapping.get("link") else None
    if isinstance(link, str) and link.startswith(("http://", "https://")):
        artifacts.append(ArtifactDraft(type="deep_link", title=f"Open in {provider_name}", external_url=link))
    return InvocationResult(state=ResultState.COMPLETED, summary=summary_text or "Done.", artifacts=artifacts)


def _parse_result(data: dict[str, Any]) -> InvocationResult:
    artifacts = [ArtifactDraft(**a) for a in data.get("artifacts", []) if isinstance(a, dict)]
    state = data.get("state", "completed")
    try:
        result_state = ResultState(state)
    except ValueError:
        result_state = ResultState.FAILED
        data["error"] = f"provider returned unknown state {state!r}"
    return InvocationResult(
        state=result_state,
        summary=data.get("summary"),
        error=data.get("error"),
        failure=FailureKind.INVOCATION_FAILED if result_state == ResultState.FAILED else None,
        artifacts=artifacts,
        external_ref=data.get("external_ref"),
    )


def _safe_base(cfg: dict[str, Any]) -> str:
    """The service's address, if Bevro is allowed to fetch it at all.

    The same check discovery made, made again at the moment of use and in
    whichever process is doing the using. A stored address is not a licence:
    the policy lives in one module and every process reads it from there.
    """
    base = str(cfg.get("base_url", "")).rstrip("/")
    if not base:
        raise urlsafety.UnsafeUrl("no base_url configured")
    urlsafety.check(base)
    return base


class HttpAdapter(BaseRuntimeAdapter):
    kind = "http"
    network = True

    def check(self, provider: ProviderSpec, secrets: dict[str, str]) -> HealthResult:
        cfg = provider.adapter_config
        try:
            base = _safe_base(cfg)
        except urlsafety.UnsafeUrl as exc:
            return HealthResult(ok=False, detail=str(exc))
        try:
            r = httpx.get(base + str(cfg.get("health_path") or "/health"), headers=_headers(cfg, secrets), timeout=10.0)
            if r.status_code == 404 and cfg.get("health_path") is None and cfg.get("invoke"):
                # No health endpoint was found at discovery: reachable is good enough.
                r = httpx.get(base + "/", headers=_headers(cfg, secrets), timeout=10.0)
            return HealthResult(ok=r.is_success, detail=None if r.is_success else f"HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            return HealthResult(ok=False, detail=str(exc))

    def invoke(self, provider: ProviderSpec, request: InvocationRequest, context: InvocationContext | None = None) -> InvocationResult:
        cfg = provider.adapter_config
        try:
            base = _safe_base(cfg)
        except urlsafety.UnsafeUrl:
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} has no address Bevro can use.", failure=FailureKind.CONFIGURATION_PROBLEM)
        profile = cfg.get("invoke") if isinstance(cfg.get("invoke"), dict) else None
        try:
            if profile:
                method = str(profile.get("method") or "POST").upper()
                path = str(profile.get("path") or "/")
                payload = _fill(profile.get("body") or {"request": "{request}"}, request.request)
                timeout = float(cfg.get("timeout_seconds", TIMEOUT))
                if method == "GET":
                    r = httpx.get(base + path, params=payload if isinstance(payload, dict) else None, headers=_headers(cfg, request.secrets), timeout=timeout)
                else:
                    r = httpx.request(method, base + path, json=payload, headers=_headers(cfg, request.secrets), timeout=timeout)
            else:
                body = {
                    "task_id": request.task_id,
                    "run_id": request.run_id,
                    "request": request.request,
                    "input": request.input,
                }
                r = httpx.post(
                    base + cfg.get("invoke_path", "/invoke"),
                    json=body,
                    headers=_headers(cfg, request.secrets),
                    timeout=float(cfg.get("timeout_seconds", TIMEOUT)),
                )
        except httpx.TimeoutException:
            log.warning("%s timed out at %s", provider.slug, base)
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} took too long to answer.", failure=FailureKind.TIMED_OUT)
        except httpx.HTTPError as exc:
            log.warning("%s unreachable at %s: %s", provider.slug, base, exc)
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} couldn't be reached.", failure=FailureKind.PROVIDER_UNAVAILABLE)
        if r.status_code in (401, 403):
            log.warning("%s refused the credentials (HTTP %s)", provider.slug, r.status_code)
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} didn't accept Bevro's credential.", failure=FailureKind.CREDENTIAL_REQUIRED)
        if not r.is_success:
            log.warning("%s answered HTTP %s", provider.slug, r.status_code)
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} couldn't take this request right now.", failure=FailureKind.INVOCATION_FAILED)
        try:
            data = r.json()
        except ValueError:
            if profile:
                text = r.text.strip()
                return _parse_profiled(text, {"text": None}, provider.name) if text else InvocationResult(state=ResultState.FAILED, error=f"{provider.name} sent back an empty answer.")
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} sent back something Bevro couldn't read.", failure=FailureKind.OUTPUT_INVALID)
        if profile:
            return _parse_profiled(data, cfg.get("response") if isinstance(cfg.get("response"), dict) else {}, provider.name)
        if not isinstance(data, dict):
            return InvocationResult(state=ResultState.FAILED, error=f"{provider.name} sent back something Bevro couldn't read.", failure=FailureKind.OUTPUT_INVALID)
        return _parse_result(data)

    def get_status(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> InvocationResult:
        cfg = provider.adapter_config
        status_path = cfg.get("status_path")
        if not status_path:
            raise NotSupported("this provider has no status endpoint configured")
        base = str(cfg.get("base_url", "")).rstrip("/")
        r = httpx.get(f"{base}{status_path}/{external_ref}", headers=_headers(cfg, secrets), timeout=10.0)
        r.raise_for_status()
        return _parse_result(r.json())

    def cancel(self, provider: ProviderSpec, external_ref: str, secrets: dict[str, str]) -> bool:
        cfg = provider.adapter_config
        cancel_path = cfg.get("cancel_path")
        if not cancel_path:
            raise NotSupported("this provider has no cancel endpoint configured")
        base = str(cfg.get("base_url", "")).rstrip("/")
        r = httpx.post(f"{base}{cancel_path}/{external_ref}", headers=_headers(cfg, secrets), timeout=10.0)
        return r.is_success


register_adapter(HttpAdapter())
