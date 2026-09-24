"""Reading an HTTP service that describes itself with OpenAPI.

Two kinds of service arrive here, and they are not the same thing:

* a **prompt** service - one operation that takes natural language and
  answers. The existing HTTP strategy handles those.
* an **operational** service - a set of typed operations that together say
  what it can do. A careers tool, an issue tracker, a finance system. There
  is no "ask me anything" endpoint; work means calling two or three of its
  operations in order.

This module reads the second kind. It never knows what any particular
service is: everything below works from the document the service publishes
about itself - its methods, tags, summaries, descriptions and schemas.

    entered URL  →  descriptor  →  operation catalogue  →  capabilities
                                                        →  scope (a profile,
                                                           a workspace, ...)

The catalogue is deliberately compact. A real descriptor runs to hundreds of
kilobytes, and none of it should ever be handed to a model wholesale.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Where a service usually publishes its description, relative to a base.
DESCRIPTOR_PATHS = ("/openapi.json", "/api/openapi.json", "/swagger.json", "/v3/api-docs")

# What a compiled catalogue may grow to before it is trimmed. A planner is
# never shown more than a small slice of this anyway.
MAX_OPERATIONS = 400
# Bumped whenever the wording a capability carries changes shape, so that
# things connected before the change are read again rather than left saying
# what an older Bevro would have said.
CAPABILITY_WORDING = 5
SUMMARY_MAX = 220


# --------------------------------------------------------------------------- what came back

@dataclass(frozen=True)
class Descriptor:
    """A real machine description, and where it was found."""

    url: str
    spec: dict[str, Any]
    # "http://host:8080" - everything is relative to this.
    origin: str
    # The path the person actually typed, e.g. "/p/someone/". Kept because
    # it may carry scope, and because reconnecting should start where they did.
    entered_path: str


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def looks_like_a_descriptor(body: Any) -> bool:
    """Is this actually an API description, rather than a page that said 200?

    A single-page application answers every path with its HTML shell, so a
    200 means nothing on its own. The document has to be the right shape.
    """
    if not isinstance(body, dict):
        return False
    if not (body.get("openapi") or body.get("swagger")):
        return False
    return isinstance(body.get("paths"), dict)


def descriptor_candidates(entered_url: str) -> list[str]:
    """Where to look, nearest first: under what was typed, then the origin.

    Someone pastes the page they are looking at. The API is usually at the
    root of the service, but not always, so both are tried - and the entered
    path first, because a service that really does publish a description
    under it means it.
    """
    origin, entered = origin_of(entered_url), path_of(entered_url)
    bases: list[str] = []
    trimmed = entered.rstrip("/")
    if trimmed:
        bases.append(origin + trimmed)
    if origin not in bases:
        bases.append(origin)
    seen: list[str] = []
    for base in bases:
        for suffix in DESCRIPTOR_PATHS:
            candidate = base + suffix
            if candidate not in seen:
                seen.append(candidate)
    return seen


# --------------------------------------------------------------------------- one operation

# What the service says about an operation matters more than its method. A
# POST that the description calls read-only is read-only; a GET is not a
# licence to assume nothing happens.
_SAYS_READ_ONLY = re.compile(r"\bread[- ]only\b|\bdoes not (?:modify|change|write)\b|\bno side[- ]effects?\b", re.I)
# Reaching outside: sending something to someone, or the service saying so.
_SAYS_EXTERNAL = re.compile(
    r"\bexternal action\b|\bsends?\s+(?:a|an|the|one)?\s*\w*\s*(message|email|notification|invite)\b"
    r"|\bcontacts?\s+(?:the\s+)?(employer|recipient|customer|user|them)\b|\bpublish(?:es)?\b",
    re.I,
)
# Writing down that something happened. A record of an external action is
# still only a record: it is not Bevro doing the thing.
_SAYS_RECORDS = re.compile(r"\brecords?\b|\bappend[- ]only\b|\blog(?:s)?\b\s+(?:that|an|the)", re.I)
_SAYS_WORK = re.compile(r"\bexecute\b|\brun(?:s)?\b|\bgenerate(?:s)?\b|\bprepare(?:s)?\b|\brender(?:s)?\b|\bbuild(?:s)?\b|\bcompute(?:s)?\b|\breconcile\b|\brewrite\b", re.I)
# Changing what is stored, in the service's own verbs.
_SAYS_MUTATES = re.compile(
    r"\b(add|remove|delete|replace|move|restore|edit|edits|update|updates|save|saves|rename|set|sets|toggle|create|creates|duplicate|undo)\b",
    re.I,
)

READ_ONLY = "read_only"
WORK_EXECUTION = "work_execution"
STATE_CHANGE = "state_change"
EXTERNAL_ACTION = "external_action"
UNKNOWN = "unknown"

# How much a person has to have asked for, before Bevro will do it.
NEEDS_APPROVAL = frozenset({EXTERNAL_ACTION})
NEEDS_CLEAR_INTENT = frozenset({STATE_CHANGE, UNKNOWN})


@dataclass
class Operation:
    """One operation, compiled down to what is worth carrying around."""

    operation_id: str
    method: str
    path: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    # {"profile_id": {"in": "path", "required": True, "type": "string"}}
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Compact: property name -> {"type", "required", "enum"}. Never the full schema.
    body: dict[str, dict[str, Any]] = field(default_factory=dict)
    # "object" / "array" / "string", plus the fields worth extracting from it.
    returns: str = ""
    returns_fields: list[str] = field(default_factory=list)
    safety: str = UNKNOWN

    @property
    def required_parameters(self) -> list[str]:
        return [name for name, spec in self.parameters.items() if spec.get("required")]

    def scope_parameters(self) -> list[str]:
        """Path parameters that look like "which of these" rather than "which one"."""
        return [name for name, spec in self.parameters.items() if spec.get("in") == "path" and SCOPE_NAME.search(name)]

    def compact(self) -> dict[str, Any]:
        """What a planner is shown. Small enough to send many of."""
        out: dict[str, Any] = {
            "operation_id": self.operation_id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "safety": self.safety,
        }
        if self.tags:
            out["tags"] = self.tags
        required = {n: s.get("type", "string") for n, s in self.parameters.items() if s.get("required")}
        optional = [n for n, s in self.parameters.items() if not s.get("required")]
        if required:
            out["required_parameters"] = required
        if optional:
            out["optional_parameters"] = optional[:12]
        if self.body:
            out["body"] = {n: {k: v for k, v in s.items() if k in ("type", "required", "enum")} for n, s in list(self.body.items())[:12]}
        if self.returns_fields:
            out["returns"] = {"kind": self.returns, "fields": self.returns_fields[:12]}
        elif self.returns:
            out["returns"] = {"kind": self.returns}
        return out


# Identifiers that scope a whole connection rather than pick one record.
SCOPE_NAME = re.compile(r"^(profile|workspace|project|tenant|account|org|organisation|organization|site|space)_?id$", re.I)
_PATH_PLACEHOLDER = re.compile(r"\{[^}]+\}")


def classify(method: str, summary: str, description: str, tags: list[str]) -> str:
    """What kind of thing this operation is, from what the service says.

    The service's own words come first. Only when it says nothing useful does
    the method decide, and then cautiously: an unexplained POST is unknown,
    not safe.
    """
    said = f"{summary} {description}"
    # "Record an external action" is a record. The service is writing down
    # what a person did elsewhere, not going and doing it.
    if _SAYS_RECORDS.search(said):
        return STATE_CHANGE
    if _SAYS_EXTERNAL.search(said):
        return EXTERNAL_ACTION
    if _SAYS_READ_ONLY.search(said):
        return READ_ONLY
    if method == "GET":
        return READ_ONLY
    if _SAYS_WORK.search(said):
        return WORK_EXECUTION
    if _SAYS_MUTATES.search(said) or method in ("PATCH", "PUT", "DELETE"):
        return STATE_CHANGE
    return UNKNOWN


# --------------------------------------------------------------------------- compiling the document

def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """Follow a local $ref. Foreign refs and deep nesting are left alone."""
    if not isinstance(node, dict) or depth > 6:
        return node if isinstance(node, dict) else {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        target: Any = spec
        for part in ref[2:].split("/"):
            if not isinstance(target, dict):
                return {}
            target = target.get(part.replace("~1", "/").replace("~0", "~"))
        return _resolve(spec, target, depth + 1)
    return node


def _properties(spec: dict[str, Any], schema: Any) -> dict[str, dict[str, Any]]:
    """The body's fields, flattened one level, with their types."""
    schema = _resolve(spec, schema)
    for combiner in ("allOf", "anyOf", "oneOf"):
        parts = schema.get(combiner)
        if isinstance(parts, list) and parts:
            merged: dict[str, dict[str, Any]] = {}
            for part in parts[:4]:
                merged.update(_properties(spec, part))
            if merged:
                return merged
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    required = set(schema.get("required") or [])
    out: dict[str, dict[str, Any]] = {}
    for name, raw in list(props.items())[:30]:
        resolved = _resolve(spec, raw)
        entry: dict[str, Any] = {"type": str(resolved.get("type") or "string"), "required": name in required}
        if isinstance(resolved.get("enum"), list):
            entry["enum"] = [str(v) for v in resolved["enum"][:12]]
        if resolved.get("description"):
            entry["description"] = " ".join(str(resolved["description"]).split())[:120]
        out[str(name)] = entry
    return out


def _response_shape(spec: dict[str, Any], op: dict[str, Any]) -> tuple[str, list[str]]:
    """What comes back, in two words and a handful of field names."""
    responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
    for code in ("200", "201", "default"):
        body = responses.get(code)
        if not isinstance(body, dict):
            continue
        content = body.get("content") if isinstance(body.get("content"), dict) else {}
        for media, entry in content.items():
            if not isinstance(entry, dict):
                continue
            schema = _resolve(spec, entry.get("schema"))
            kind = str(schema.get("type") or ("object" if schema.get("properties") else "")) or media
            if kind == "array":
                items = _resolve(spec, schema.get("items"))
                return "array", sorted(_properties(spec, items))[:12]
            return kind, sorted(_properties(spec, schema))[:12]
    return "", []


def compile_catalogue(spec: dict[str, Any]) -> list[Operation]:
    """Turn a descriptor into a compact list of operations.

    This runs once, at discovery, and is kept with the runtime. Nothing
    afterwards reads the original document.
    """
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    catalogue: list[Operation] = []
    for path, item in paths.items():
        if not isinstance(item, dict) or len(catalogue) >= MAX_OPERATIONS:
            continue
        shared = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for method, raw in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete") or not isinstance(raw, dict):
                continue
            summary = " ".join(str(raw.get("summary") or "").split())[:SUMMARY_MAX]
            description = " ".join(str(raw.get("description") or "").split())[:600]
            tags = [str(t) for t in (raw.get("tags") or []) if isinstance(t, str)][:6]
            parameters: dict[str, dict[str, Any]] = {}
            for param in [*shared, *(raw.get("parameters") or [])]:
                param = _resolve(spec, param)
                name = param.get("name")
                if not isinstance(name, str):
                    continue
                schema = _resolve(spec, param.get("schema"))
                parameters[name] = {
                    "in": str(param.get("in") or "query"),
                    "required": bool(param.get("required")) or param.get("in") == "path",
                    "type": str(schema.get("type") or "string"),
                }
            content = ((raw.get("requestBody") or {}).get("content") or {}) if isinstance(raw.get("requestBody"), dict) else {}
            body_schema = (content.get("application/json") or {}).get("schema") if isinstance(content.get("application/json"), dict) else None
            returns, fields = _response_shape(spec, raw)
            catalogue.append(
                Operation(
                    operation_id=str(raw.get("operationId") or f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}")[:120],
                    method=method.upper(),
                    path=str(path),
                    summary=summary or description[:SUMMARY_MAX],
                    tags=tags,
                    parameters=parameters,
                    body=_properties(spec, body_schema) if body_schema else {},
                    returns=returns,
                    returns_fields=fields,
                    safety=classify(method.upper(), summary, description, tags),
                )
            )
    return catalogue


def catalogue_to_json(catalogue: list[Operation]) -> list[dict[str, Any]]:
    """The form kept on the runtime profile."""
    return [
        {
            "operation_id": op.operation_id,
            "method": op.method,
            "path": op.path,
            "summary": op.summary,
            "tags": op.tags,
            "parameters": op.parameters,
            "body": op.body,
            "returns": op.returns,
            "returns_fields": op.returns_fields,
            "safety": op.safety,
        }
        for op in catalogue
    ]


def catalogue_from_json(raw: Any) -> list[Operation]:
    out: list[Operation] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not entry.get("operation_id"):
            continue
        out.append(
            Operation(
                operation_id=str(entry["operation_id"]),
                method=str(entry.get("method") or "GET").upper(),
                path=str(entry.get("path") or "/"),
                summary=str(entry.get("summary") or ""),
                tags=[str(t) for t in entry.get("tags") or []],
                parameters=entry.get("parameters") if isinstance(entry.get("parameters"), dict) else {},
                body=entry.get("body") if isinstance(entry.get("body"), dict) else {},
                returns=str(entry.get("returns") or ""),
                returns_fields=[str(f) for f in entry.get("returns_fields") or []],
                safety=str(entry.get("safety") or UNKNOWN),
            )
        )
    return out


# --------------------------------------------------------------------------- what it can do, in human terms

# A capability is something a person would ask for. Operations are how it is
# done. These are read off the service's own grouping (its tags) and the
# words it uses, never from a list of known products.
_STOP_TAGS = {"default", "health", "internal", "debug", "meta", "system"}


def _titled(text: str) -> str:
    words = re.split(r"[_\-\s]+", text.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


# Path segments that name no subject: versions, the word "api", and so on.
_PLUMBING = frozenset({"api", "v1", "v2", "v3", "rest", "public", "internal", "index"})


def _subject_words(ops: list["Operation"]) -> list[str]:
    """The nouns a service puts in its own addresses for this group.

    A service's tag and its URLs often disagree - "shortlist" over
    /api/v1/opportunities - and the person asking may use either. Both are
    the service's own words, so both are worth knowing.
    """
    words: list[str] = []
    for op in ops:
        for segment in str(op.path or "").split("/"):
            segment = segment.strip()
            if not segment or segment.startswith("{"):
                continue
            word = re.sub(r"[^a-z0-9]+", " ", segment.lower()).strip()
            if not word or word in _PLUMBING or len(word) < 4 or re.fullmatch(r"v\d+", word):
                continue
            if word not in words:
                words.append(word)
    return words[:6]


def capabilities_from_operations(catalogue: list[Operation]) -> list[dict[str, str]]:
    """Group operations into abilities a person would recognise.

    One group per tag the service uses. The title is a phrase built from what
    the group's operations actually do - "Review opportunities" rather than
    "Vacancies" - because a tag is a name in someone's code, and the person
    reading it did not write that code. `app/connect/phrasing.py` does the
    English; the group's own words are kept in `description` and `terms`.

    Forty operations do not become forty capabilities.
    """
    from app.connect import phrasing
    groups: dict[str, list[Operation]] = {}
    for op in catalogue:
        for tag in op.tags or ["general"]:
            if tag.lower() in _STOP_TAGS:
                continue
            groups.setdefault(tag, []).append(op)
    phrased: list[tuple[float, dict[str, Any]]] = []
    for tag, ops in groups.items():
        readable = [o for o in ops if o.safety == READ_ONLY and o.summary] or [o for o in ops if o.summary]
        description = readable[0].summary if readable else f"{len(ops)} operations"
        phrase = phrasing.phrase_for(tag, [o.compact() for o in ops])
        capability: dict[str, Any] = {
            "id": re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_") or "general",
            # What this group can be asked for, in words: "Review opportunities".
            "title": phrase.title,
            "description": description[:200],
            # The same thing in the form a sentence needs: "review opportunities",
            # kept split so a sentence can conjugate the verb without guessing.
            "action": phrase.action,
            "verb": phrase.verb,
            "subject": phrase.subject,
            "also": phrase.also,
            "wording": CAPABILITY_WORDING,
            # How much of a reason this group is to have connected the thing.
            "weight": round(phrase.weight, 2),
        }
        terms = [w for w in _subject_words(ops) if w != capability["id"]]
        if terms:
            capability["terms"] = terms
        phrased.append((phrase.weight, capability))
    # Worth-first: what someone came for before the machinery it runs on.
    phrased.sort(key=lambda pair: -pair[0])
    return [capability for _weight, capability in phrased[:8]]


def is_operational(catalogue: list[Operation]) -> bool:
    """Enough typed operations that this is a system, not a single endpoint."""
    useful = [op for op in catalogue if op.tags or op.summary]
    return len(useful) >= 3


# --------------------------------------------------------------------------- scope

def scope_candidates(entered_path: str) -> list[str]:
    """Identifiers a UI route might be carrying.

    "/p/someone/" offers "p" and "someone"; which - if either - means
    anything is decided by asking the API, not by the shape of the path.
    """
    parts = [p for p in entered_path.split("/") if p and not p.startswith("_")]
    return [p for p in parts if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", p)][-4:]


def listing_operations(catalogue: list[Operation]) -> list[Operation]:
    """Read operations that list the things a scope parameter names.

    A scope is confirmed by finding it in the service's own list of them:
    /profiles/{profile_id} is scoped by profile_id, and /profiles lists them.
    """
    scoped: set[str] = set()
    for op in catalogue:
        scoped.update(op.scope_parameters())
    out: list[Operation] = []
    for name in sorted(scoped):
        stem = name[: -len("_id")] if name.lower().endswith("_id") else name
        for op in catalogue:
            if op.method != "GET" or op.parameters.get(name) or _PATH_PLACEHOLDER.search(op.path):
                continue
            if re.search(rf"/{re.escape(stem)}s?/?$", op.path, re.I):
                out.append(op)
                break
    return out


def identifiers_in(payload: Any, key_hint: str) -> list[str]:
    """Every value that could be an id of `key_hint`, from a listing response."""
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                rows = value
                break
    found: list[str] = []
    for row in rows[:200]:
        if isinstance(row, str):
            found.append(row)
        elif isinstance(row, dict):
            for key in (key_hint, "id", "slug", "name"):
                value = row.get(key)
                if isinstance(value, (str, int)):
                    found.append(str(value))
                    break
    return found


def labels_in(payload: Any, key_hint: str) -> dict[str, str]:
    """Human names for those ids, when the service offers any."""
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                rows = value
                break
    labels: dict[str, str] = {}
    for row in rows[:200]:
        if not isinstance(row, dict):
            continue
        ident = next((str(row[k]) for k in (key_hint, "id", "slug", "name") if isinstance(row.get(k), (str, int))), None)
        if ident is None:
            continue
        label = next((str(row[k]) for k in ("display_name", "name", "title", "label") if isinstance(row.get(k), str)), ident)
        labels[ident] = label
    return labels


def pretty(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True)[:4000]
