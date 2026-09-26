"""Shared shapes for local project inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entrypoint:
    """One way the project can be started. `argv` is server-side (it may hold
    an absolute interpreter path); `label` is what the person sees."""

    argv: list[str]
    label: str
    confidence: str  # high | medium | low
    source: str  # evidence sentence, no absolute paths
    input: dict[str, Any] = field(default_factory=lambda: {"mode": "argument"})
    input_known: bool = False
    # "command" (a program to run per task), "mcp" (speaks MCP on stdio), "http" (a server to start)
    mechanism: str = "command"
    # The runtime kind this entry point is, when it is not the project's own language
    # (a wrapper script in a Python project is a plain CLI). Empty: derive from the language.
    runtime_kind: str = ""
    # The program's own self-check, when its interface declares one: the
    # option to add, like ["--self-test"]. Test runs it; nothing else does.
    self_check: list[str] = field(default_factory=list)


@dataclass
class Finding:
    """What an inspector learned. Combined by the local strategy."""

    kind: str  # "python" | "node" | "docker"
    name: str | None = None
    description: str | None = None
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entrypoints: list[Entrypoint] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    frameworks: set[str] = field(default_factory=set)  # "fastapi", "flask", "mcp", "express", ...
    # HTTP services the project would expose once started: [{"port": 8000, "health_path": "/health"}]
    services: list[dict[str, Any]] = field(default_factory=list)
    env_secret_names: list[str] = field(default_factory=list)
    # Extra non-secret environment the entry point needs, e.g. PYTHONPATH for an src/ layout.
    env: dict[str, str] = field(default_factory=dict)
    # The project loads its own .env (python-dotenv, dotenv): it can carry its own credentials.
    loads_dotenv: bool = False
    # Compose services a one-off container could be run from, whatever they
    # publish: {"file", "service", "entrypoint", "built_here", "ports", "svc"}.
    containers: list[dict[str, Any]] = field(default_factory=list)


CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def best_entrypoint(entrypoints: list[Entrypoint]) -> Entrypoint | None:
    return sorted(entrypoints, key=lambda e: (CONF_ORDER[e.confidence], 0 if e.input_known else 1))[0] if entrypoints else None


# Environment variables agents typically need, by dependency name.
SECRET_BY_DEPENDENCY = {
    "openai": "OPENAI_API_KEY",
    "openai-agents": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google-generativeai": "GOOGLE_API_KEY",
    "google-genai": "GOOGLE_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "groq": "GROQ_API_KEY",
    "@anthropic-ai/sdk": "ANTHROPIC_API_KEY",
    "@google/generative-ai": "GOOGLE_API_KEY",
}

from app.connect.draft import SECRET_LABELS  # noqa: E402 - the one table of credential names

# Request-taking options, in order of preference.
INPUT_FLAGS = ("--topic", "--query", "--question", "--prompt", "--request", "--task", "--input", "--text", "--message", "--goal")
INPUT_POSITIONALS = ("topic", "query", "question", "prompt", "request", "task", "input", "text", "message", "goal")
