"""URL discovery: read what a service publishes about itself.

Probes, all GET and all optional:
  /.well-known/bevro.json   an optional Bevro manifest (fast path, never required)
  /openapi.json, /api/openapi.json   OpenAPI: operations, descriptions, auth
  /health, /healthz, /api/health     a health endpoint to remember
  /                                  a title, if nothing else
and, when the address looks like an MCP endpoint or nothing else answered,
one MCP `initialize` (read-only by the protocol's definition).

Nothing that changes state is ever called during discovery.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from adapters import urlsafety
from adapters.runtime import CredentialStrategy, Credentials, RuntimeKind
from app.connect import openapi
from app.connect.capabilities import capability_from_tool, infer_capabilities
from app.connect.draft import DraftAuth, DraftCapability, ProviderDraft
from app.connect.inspect import humanise
from app.connect.runtimes import http_runtime
from app.connect.strategies.base import DiscoveryContext, DiscoveryFailed, NotReachable


class _NoContact(Exception):
    """Nothing accepted a connection - as opposed to answering badly."""


UNREACHABLE_MESSAGE = "Nothing answered at that address. Check that it is running and reachable from this machine."
from app.connect.targets import ConnectTarget

OPENAPI_PATHS = ("/openapi.json", "/api/openapi.json", "/openapi.yaml")
HEALTH_PATHS = ("/health", "/healthz", "/api/health", "/status")
INVOKE_PATH_HINT = re.compile(r"/(invoke|ask|query|run|task|tasks|chat|complete|completion|search|research|answer|generate|execute|agent|prompt)s?/?$", re.I)
REQUEST_FIELDS = ("request", "query", "prompt", "question", "input", "text", "topic", "message", "task", "goal")
RESPONSE_FIELDS = ("summary", "answer", "result", "output", "text", "response", "content", "message")
LINK_FIELDS = ("link", "url", "href", "deep_link", "review_url")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _split_base(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return base, (parts.path or "/").rstrip("/") or ""


class HttpDiscoveryStrategy:
    name = "http"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def supports(self, target: ConnectTarget) -> bool:
        return target.kind in ("url", "mcp")

    def discover(self, target: ConnectTarget, context: DiscoveryContext) -> ProviderDraft:
        url = target.value
        if context.transport is None and self._transport is not None:
            context.transport = self._transport
        headers = {"Accept": "application/json"}
        token = context.secrets.get("api_key")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        transport = context.transport or self._transport
        try:
            urlsafety.check(url)
        except urlsafety.UnsafeUrl as exc:
            raise DiscoveryFailed(str(exc)) from None
        with urlsafety.client(timeout=context.timeout, transport=transport, headers=headers) as client:
            # 0. Is anything there at all? Asked once, before any paths are
            # walked. An address this process cannot reach fails the same way
            # for every path it is asked about, and discovering that five
            # times over is a minute the person spends watching nothing.
            try:
                reached, title = _root(client, url)
            except _NoContact:
                raise NotReachable(UNREACHABLE_MESSAGE) from None

            # 1. An address that looks like an MCP endpoint: ask it first.
            if target.kind == "mcp":
                draft = _probe_mcp(url, context, headers)
                if draft is not None:
                    return draft
            base, prefix = _split_base(url)
            bases = [base + prefix, base] if prefix else [base]
            # 2. What the service publishes about itself: a Bevro manifest (optional), then OpenAPI.
            manifest = _get_json(client, bases, "/.well-known/bevro.json")
            if isinstance(manifest, dict) and manifest.get("name"):
                return _from_manifest(manifest, bases[0], url)
            health_path = _find_health(client, bases[0])
            # A service describes itself somewhere: under the path that was
            # typed, or at its origin. A page that answers 200 with its HTML
            # shell is not a description, so the document is checked, not the
            # status code.
            found = _find_descriptor(client, url)
            if found is not None:
                catalogue = openapi.compile_catalogue(found.spec)
                prompt_draft = _from_openapi(found.spec, found.origin, health_path)
                # A single operation that takes natural language is the simple
                # case. Anything else is a system of typed operations, and is
                # read as one.
                if not prompt_draft.invocable and openapi.is_operational(catalogue):
                    return _from_operational(client, found, catalogue, health_path)
                return prompt_draft
            # 3. Maybe it is an MCP server after all; one initialize is all it costs.
            if target.kind != "mcp":
                draft = _probe_mcp(url, context, headers)
                if draft is not None:
                    return draft
            # 4. Something that does not describe itself, or nothing usable.
            if not reached:
                raise NotReachable(UNREACHABLE_MESSAGE)
            return _from_bare(url, title, health_path)


def _find_descriptor(client: httpx.Client, entered_url: str) -> openapi.Descriptor | None:
    """The service's own machine description, if it really publishes one.

    A single-page application answers every path with its HTML shell, so
    `/p/someone/openapi.json` can return 200 and mean nothing. Both the
    content type and the shape of the document have to agree before it is
    believed.
    """
    for candidate in openapi.descriptor_candidates(entered_url):
        try:
            response = client.get(candidate)
        except httpx.HTTPError:
            continue
        if response.status_code != 200:
            continue
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and not candidate.endswith((".yaml", ".yml")):
            continue  # an HTML page saying 200 is not a description
        try:
            body = response.json()
        except ValueError:
            continue
        if not openapi.looks_like_a_descriptor(body):
            continue
        return openapi.Descriptor(
            url=candidate,
            spec=body,
            origin=openapi.origin_of(entered_url),
            entered_path=openapi.path_of(entered_url),
        )
    return None


def _resolve_scope(client: httpx.Client, base: str, catalogue: list[openapi.Operation], entered_path: str) -> tuple[dict[str, str], list[dict[str, str]], list[str]]:
    """Which profile, workspace or tenant this connection is for.

    The route someone pasted may carry it - "/p/someone/" suggests someone -
    but a guess from a URL is only a candidate. It counts when the API's own
    listing of those things contains it. Nothing here knows what "/p/" means.

    Returns (context, choices, evidence). `choices` is filled only when the
    service has several and the route did not say which.
    """
    context: dict[str, str] = {}
    choices: list[dict[str, str]] = []
    evidence: list[str] = []
    candidates = openapi.scope_candidates(entered_path)
    for listing in openapi.listing_operations(catalogue)[:3]:
        scoped = next((n for op in catalogue for n in op.scope_parameters()), None)
        if scoped is None:
            continue
        try:
            response = client.get(base + listing.path)
        except httpx.HTTPError:
            continue
        if response.status_code != 200 or "json" not in response.headers.get("content-type", "").lower():
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        known = openapi.identifiers_in(payload, scoped)
        if not known:
            continue
        labels = openapi.labels_in(payload, scoped)
        confirmed = next((c for c in reversed(candidates) if c in known), None)
        if confirmed is not None:
            context[scoped] = confirmed
            evidence.append(f"The address names {labels.get(confirmed, confirmed)}, and the service confirms it")
        elif len(known) == 1:
            context[scoped] = known[0]
            evidence.append(f"One {scoped.replace('_id', '').replace('_', ' ')} exists, so this connection uses it")
        else:
            choices = [{"value": k, "label": labels.get(k, k)} for k in known[:20]]
            evidence.append(f"The service has {len(known)} to choose from")
        break
    return context, choices, evidence


def _from_operational(client: httpx.Client, found: openapi.Descriptor, catalogue: list[openapi.Operation], health_path: str | None) -> ProviderDraft:
    """A service whose work is several typed operations, not one prompt."""
    info = found.spec.get("info") if isinstance(found.spec.get("info"), dict) else {}
    name = str(info.get("title") or "Service")[:120]
    description = " ".join(str(info.get("description") or "").split())[:2000]
    base = found.origin

    context, choices, scope_evidence = _resolve_scope(client, base, catalogue, found.entered_path)
    # Everything the compiler worked out travels with the draft, so the
    # preview says exactly what Agents will say once it is connected.
    capabilities = [DraftCapability.model_validate(c) for c in openapi.capabilities_from_operations(catalogue)]
    usable = [op for op in catalogue if op.safety in (openapi.READ_ONLY, openapi.WORK_EXECUTION)]

    auth, auth_config = _auth_from(found.spec)
    config: dict[str, Any] = {
        "base_url": base,
        "descriptor_url": found.url,
        "app_url": base + found.entered_path,
        "operations": openapi.catalogue_to_json(catalogue),
        "context": context,
        "health_path": health_path,
        **auth_config,
    }
    evidence = [
        f"Describes itself with OpenAPI at {found.url.replace(base, '') or '/'}",
        f"{len(catalogue)} operations, grouped into {len(capabilities)} things it can do",
        *scope_evidence,
    ]
    warnings: list[str] = []
    if choices:
        warnings.append("Choose which one this connection is for before connecting.")
    if not usable:
        warnings.append("Bevro found no operation it could safely run here.")

    runtime = http_runtime(
        "openapi",
        kind=RuntimeKind.OPENAPI,
        adapter={"kind": "openapi", "config": config},
        display_name="Connected over the network",
        # Not runnable until it is known which one this connection is for.
        availability="ready" if usable and not choices else "not_invocable",
        confidence="high",
        credentials=Credentials(
            strategy=CredentialStrategy.BEVRO_MANAGED if auth.required else CredentialStrategy.NONE,
            names=[auth.secret_name] if auth.secret_name else [],
            required_from_user=auth.required,
        ),
        evidence=evidence,
        warnings=warnings,
        target=base,
    )
    draft = ProviderDraft(
        name=name,
        description=description,
        capabilities=capabilities,
        mechanism="http",
        mechanism_label="Connected over the network",
        adapter={"kind": "openapi", "config": config},
        invocation_label=f"{len(usable)} operations Bevro may use",
        availability="ready" if usable else "needs_start",
        confidence="high",
        evidence=evidence,
        warnings=warnings,
        app_url=base + found.entered_path,
        auth=auth,
        invocable=bool(usable) and not choices,
        source={
            "kind": "http",
            "entered_url": base + found.entered_path,
            "origin": base,
            "ui_base_path": found.entered_path,
            "descriptor_url": found.url,
            "connection_context": context,
            "discovery_method": "openapi",
        },
    )
    draft.scope_choices = choices
    return draft.with_runtimes([runtime], active_id="openapi")


def _auth_from(spec: dict[str, Any]) -> tuple[DraftAuth, dict[str, Any]]:
    """What the service says it needs, if anything."""
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {}) if isinstance(spec.get("components"), dict) else {}
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    wants = bool(spec.get("security")) or any(
        isinstance(op, dict) and op.get("security") for item in paths.values() if isinstance(item, dict) for op in item.values()
    )
    if not wants:
        return DraftAuth(), {}
    for _name, scheme in schemes.items():
        if not isinstance(scheme, dict):
            continue
        if scheme.get("type") == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
            return (
                DraftAuth(required=True, secret_name="api_key", label="API token", hint="Sent as a bearer token. Stored encrypted; never shown again."),
                {"auth": {"type": "bearer", "secret": "api_key"}},
            )
        if scheme.get("type") == "apiKey" and scheme.get("in") == "header" and scheme.get("name"):
            return (
                DraftAuth(required=True, secret_name="api_key", label="API key", hint=f"Sent in the {scheme['name']} header. Stored encrypted; never shown again."),
                {"auth": {"type": "header", "name": str(scheme["name"]), "secret": "api_key"}},
            )
    return DraftAuth(), {}


def _get_json(client: httpx.Client, bases: list[str], paths: str | tuple[str, ...]) -> Any:
    for base in bases:
        for path in (paths,) if isinstance(paths, str) else paths:
            try:
                r = client.get(base + path)
            except httpx.HTTPError:
                return None
            if r.status_code != 200:
                continue
            try:
                return r.json()
            except ValueError:
                if path.endswith(".yaml"):
                    try:
                        import yaml

                        return yaml.safe_load(r.text)
                    except Exception:  # noqa: BLE001
                        continue
                continue
    return None


def _find_health(client: httpx.Client, base: str) -> str | None:
    for path in HEALTH_PATHS:
        try:
            r = client.get(base + path)
        except httpx.HTTPError:
            return None
        if r.status_code == 200:
            return path
    return None


def _root(client: httpx.Client, url: str) -> tuple[bool, str | None]:
    """Did anything answer here, and what does it call itself?

    "Answered" and "answered usefully" are separated from "could not be
    reached at all", because only the last one is a statement about this
    process rather than about the service.
    """
    try:
        r = client.get(url, headers={"Accept": "text/html, application/json"})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise _NoContact() from exc
    except httpx.HTTPError:
        return False, None
    if r.status_code >= 500:
        return False, None
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            data = r.json()
            if isinstance(data, dict):
                for key in ("name", "title", "app", "service"):
                    if isinstance(data.get(key), str):
                        return True, data[key]
        except ValueError:
            pass
    m = _TITLE.search(r.text[:20_000] if "html" in ctype else "")
    return True, (" ".join(m.group(1).split())[:80] if m else None)


def _probe_mcp(url: str, context: DiscoveryContext, headers: dict[str, str]) -> ProviderDraft | None:
    from app.connect.strategies.mcp import McpDiscoveryStrategy

    try:
        return McpDiscoveryStrategy().discover_url(url, context, headers)
    except DiscoveryFailed:
        return None


def _from_manifest(manifest: dict[str, Any], base_url: str, url: str) -> ProviderDraft:
    caps: list[DraftCapability] = []
    for item in manifest.get("capabilities") or []:
        if isinstance(item, dict) and item.get("id"):
            caps.append(DraftCapability(id=str(item["id"])[:80], title=str(item.get("title") or humanise(str(item["id"])))[:80], description=(str(item["description"])[:200] if item.get("description") else None)))
        elif isinstance(item, str):
            caps.append(capability_from_tool(item, None))
    auth_block = manifest.get("auth") if isinstance(manifest.get("auth"), dict) else {}
    auth = DraftAuth(required=bool(auth_block), secret_name="api_key", label="API token", hint="Sent as a bearer token. Stored encrypted; never shown again.") if auth_block else DraftAuth()
    config: dict[str, Any] = {"base_url": base_url}
    if manifest.get("invoke_path"):
        config["invoke_path"] = str(manifest["invoke_path"])
    if manifest.get("health_path"):
        config["health_path"] = str(manifest["health_path"])
    if auth_block:
        config["auth"] = {"type": "bearer", "secret": "api_key"}
    evidence = ["Publishes a Bevro manifest at /.well-known/bevro.json"]
    draft = ProviderDraft(
        name=str(manifest["name"])[:120],
        description=str(manifest.get("description") or "")[:2000],
        capabilities=caps[:8],
        mechanism="http",
        mechanism_label="API",
        adapter={"kind": "http", "config": config},
        invocation_label=url,
        availability="ready",
        confidence="high",
        evidence=evidence,
        app_url=str(manifest["app_url"]) if isinstance(manifest.get("app_url"), str) else None,
        auth=auth,
    )
    creds = Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["api_key"], required_from_user=True, note=auth.hint) if auth_block else Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED)
    return draft.with_runtimes([http_runtime("http", kind=RuntimeKind.HTTP, adapter=draft.adapter, display_name="Connected over the network", availability="ready", confidence="high", credentials=creds, evidence=evidence)])


def _schema_props(spec: dict[str, Any], schema: Any) -> dict[str, Any]:
    """Resolve one level of $ref and return the object's properties."""
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema and isinstance(schema["$ref"], str) and schema["$ref"].startswith("#/"):
        node: Any = spec
        for part in schema["$ref"][2:].split("/"):
            node = node.get(part) if isinstance(node, dict) else None
        schema = node if isinstance(node, dict) else {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _from_openapi(spec: dict[str, Any], base_url: str, health_path: str | None) -> ProviderDraft:
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    title = str(info.get("title") or "Service")[:120]
    description = " ".join(str(info.get("description") or "").split())[:2000]
    version = f" {info['version']}" if info.get("version") else ""
    evidence = [f"OpenAPI description found ({title}{version})"]
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}

    bevro_contract = False
    candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    tags: list[str] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("post", "get") or not isinstance(op, dict):
                continue
            for tag in op.get("tags") or []:
                if isinstance(tag, str) and tag not in tags:
                    tags.append(tag)
            body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}
            props = _schema_props(spec, body.get("schema"))
            if method.lower() == "post" and path.rstrip("/").endswith("/invoke") and {"request", "task_id"} <= set(props):
                bevro_contract = True
            score = 0
            if method.lower() == "post":
                score += 1
            if INVOKE_PATH_HINT.search(path):
                score += 2
            field = next((f for f in REQUEST_FIELDS if f in props and (props[f] or {}).get("type", "string") == "string"), None)
            if field:
                score += 2
            if len(props) == 1 and field:
                score += 1
            if "{" in path:
                score -= 3
            if score >= 3 and field:
                candidates.append((score, method.upper(), path, {"field": field, "op": op}))
    evidence.append(f"{sum(1 for _p, i in paths.items() if isinstance(i, dict) for m in i if m.lower() in ('get', 'post', 'put', 'delete', 'patch'))} operation(s) described")

    auth = DraftAuth()
    config: dict[str, Any] = {"base_url": base_url, "health_path": health_path}
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {}) if isinstance(spec.get("components"), dict) else {}
    if spec.get("security") or any(isinstance(op, dict) and op.get("security") for item in paths.values() if isinstance(item, dict) for op in item.values()):
        for _name, scheme in schemes.items():
            if not isinstance(scheme, dict):
                continue
            if scheme.get("type") == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                config["auth"] = {"type": "bearer", "secret": "api_key"}
                auth = DraftAuth(required=True, secret_name="api_key", label="API token", hint="Sent as a bearer token. Stored encrypted; never shown again.")
                break
            if scheme.get("type") == "apiKey" and scheme.get("in") == "header" and scheme.get("name"):
                config["auth"] = {"type": "header", "name": str(scheme["name"]), "secret": "api_key"}
                auth = DraftAuth(required=True, secret_name="api_key", label="API key", hint=f"Sent in the {scheme['name']} header. Stored encrypted; never shown again.")
                break
        if not auth.required:
            evidence.append("The API asks for authentication Bevro doesn't support yet")

    capabilities: list[DraftCapability] = []
    for tag in tags[:6]:
        capabilities.append(capability_from_tool(tag, None))
    if not capabilities:
        for _score, _method, path, extra in sorted(candidates, key=lambda c: -c[0])[:5]:
            op = extra["op"]
            capabilities.append(capability_from_tool(str(op.get("operationId") or path.strip("/").replace("/", "_")), op.get("summary") or op.get("description")))
    if not capabilities:
        capabilities = infer_capabilities(title, description, weights=(2.0, 2.0))

    if bevro_contract:
        config["invoke_path"] = next(p for p in paths if p.rstrip("/").endswith("/invoke"))
        evidence.append("Speaks the Bevro HTTP contract (/invoke)")
        confidence = "high"
        label = f"{base_url}{config['invoke_path']}"
        warnings: list[str] = []
        invocable = True
    elif candidates:
        score, method, path, extra = sorted(candidates, key=lambda c: -c[0])[0]
        op = extra["op"]
        config["invoke"] = {"method": method, "path": path, "body": {extra["field"]: "{request}"}}
        responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
        ok = responses.get("200") or responses.get("201") or {}
        resp_props = _schema_props(spec, (((ok.get("content") or {}).get("application/json") or {}).get("schema")))
        mapping: dict[str, str] = {}
        for field in RESPONSE_FIELDS:
            if field in resp_props:
                mapping["text"] = field
                mapping.setdefault("summary", field)
                break
        link = next((f for f in LINK_FIELDS if f in resp_props), None)
        if link:
            mapping["link"] = link  # a deep link back into the service, shown as "Open in X"
        if mapping:
            config["response"] = mapping
        evidence.append(f"{method} {path} takes a '{extra['field']}' and looks like the way to ask it something")
        confidence = "high" if score >= 5 else "medium"
        label = f"{method} {path}"
        warnings = []
        invocable = True
    else:
        confidence = "low"
        label = base_url
        warnings = ["I couldn't find an operation that takes a plain request. You can point Bevro at one under Advanced setup."]
        invocable = False
    if not capabilities:
        confidence = "low"
    draft = ProviderDraft(
        name=title,
        description=description,
        capabilities=capabilities[:8],
        mechanism="http",
        mechanism_label="API",
        adapter={"kind": "http", "config": config},
        invocation_label=label,
        availability="ready",
        confidence=confidence,
        evidence=evidence,
        warnings=warnings,
        auth=auth,
        invocable=invocable,
    )
    creds = Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["api_key"], required_from_user=True, note=auth.hint) if auth.required else Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED)
    rt = http_runtime("http", kind=RuntimeKind.HTTP, adapter=draft.adapter, display_name="Connected over the network", availability="ready" if invocable else "not_invocable", confidence=confidence, credentials=creds, evidence=evidence, warnings=warnings, accepts_prompt=invocable)
    return draft.with_runtimes([rt])


def _from_bare(url: str, title: str | None, health_path: str | None) -> ProviderDraft:
    host = urlsplit(url).hostname or url
    name = title or humanise(host.split(".")[0])
    base, prefix = _split_base(url)
    evidence = ["Something answers at that address" + (f" (health at {health_path})" if health_path else "")]
    warnings = ["It doesn't describe itself (no OpenAPI, no MCP, no Bevro manifest). Tell Bevro what it does, or describe the request format under Advanced setup."]
    draft = ProviderDraft(
        name=name[:120],
        description="",
        capabilities=[],
        mechanism="http",
        mechanism_label="API",
        adapter={"kind": "http", "config": {"base_url": base + prefix, "health_path": health_path}},
        invocation_label=url,
        availability="ready",
        confidence="low",
        evidence=evidence,
        warnings=warnings,
        invocable=False,
    )
    rt = http_runtime("http", kind=RuntimeKind.HTTP, adapter=draft.adapter, display_name="Connected over the network", availability="not_invocable", confidence="low", credentials=Credentials(strategy=CredentialStrategy.UNKNOWN), evidence=evidence, warnings=warnings, accepts_prompt=False)
    return draft.with_runtimes([rt])
