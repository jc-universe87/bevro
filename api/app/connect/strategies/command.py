"""Command discovery: parse and classify only. Nothing is executed until a task runs."""

from __future__ import annotations

import re

from app.connect.capabilities import infer_capabilities
from app.connect.draft import DraftAuth, ProviderDraft
from app.connect.inspect import humanise
from app.connect.runtimes import cli_runtime
from adapters.runtime import CredentialStrategy, Credentials, RuntimeKind
from app.connect.strategies.base import DiscoveryContext
from app.connect.strategies.project import INPUT_FLAGS
from app.connect.targets import ConnectTarget, is_known_launcher

_MODULE = re.compile(r"^[A-Za-z_][\w.]*$")


def name_from_argv(argv: tuple[str, ...]) -> str:
    words = list(argv)
    program = words[0].rsplit("/", 1)[-1]
    if program.startswith("python") and len(words) >= 3 and words[1] == "-m" and _MODULE.match(words[2]):
        return humanise(words[2].split(".")[0])
    if program in ("npx", "uvx", "pipx", "bunx") and len(words) >= 2:
        arg = next((w for w in words[1:] if not w.startswith("-")), words[1])
        return humanise(arg.split("/")[-1].split("@")[0])
    if program in ("node", "deno", "bun", "python", "python3") and len(words) >= 2:
        return humanise(words[1].rsplit("/", 1)[-1].rsplit(".", 1)[0])
    if program == "docker" and "run" in words:
        image = next((w for w in words[words.index("run") + 1:] if not w.startswith("-")), None)
        if image:
            return humanise(image.split("/")[-1].split(":")[0])
    return humanise(program)


class CommandStrategy:
    name = "command"

    def supports(self, target: ConnectTarget) -> bool:
        return target.kind == "command"

    def discover(self, target: ConnectTarget, context: DiscoveryContext) -> ProviderDraft:
        argv = list(target.argv)
        name = name_from_argv(target.argv)
        known = is_known_launcher(argv[0])
        warnings: list[str] = []
        evidence = [f"Program: {argv[0].rsplit('/', 1)[-1]}"]
        if not known:
            warnings.append("Bevro doesn't recognise this program. Check the command before connecting; Advanced setup has the details.")
        # If the command already carries a request option, keep it as the input flag.
        flag = next((w for w in argv[1:] if w in INPUT_FLAGS), None)
        if flag:
            index = argv.index(flag)
            argv = argv[:index] + argv[index + 1:]
            if index < len(argv) and not argv[index].startswith("-"):
                del argv[index]
            input_mode = {"mode": "flag", "flag": flag}
            evidence.append(f"Takes the request with {flag}")
        else:
            input_mode = {"mode": "argument"}
            warnings.append("The request will be passed as the last argument. Change that under Advanced setup if the program expects something else.")
        capabilities = infer_capabilities(name, " ".join(argv), weights=(2.0, 1.0))
        adapter = {
            "kind": "command",
            "config": {"argv": argv, "input": input_mode, "output": {"modes": ["stdout", "files"]}, "env": {}, "secret_env": [], "timeout_seconds": 1800},
        }
        confidence = "medium" if known and flag else "low"
        draft = ProviderDraft(
            name=name,
            description="",
            capabilities=capabilities,
            mechanism="command",
            mechanism_label="Command",
            adapter=adapter,
            invocation_label=" ".join(target.argv),
            availability="needs_worker",
            confidence=confidence,
            evidence=evidence,
            warnings=warnings,
            auth=DraftAuth(),
            invocable=True,
        )
        rt = cli_runtime("cli", kind=RuntimeKind.CLI, adapter=adapter, display_name="Runs on this machine", availability="needs_worker", confidence=confidence, credentials=Credentials(strategy=CredentialStrategy.INHERITED_ENVIRONMENT, note="Runs with the worker's own environment."), evidence=evidence, warnings=warnings)
        return draft.with_runtimes([rt])
