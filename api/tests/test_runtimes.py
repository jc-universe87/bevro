"""Runtime profiles: the model, ranking and selection, migration of old records, the contract."""

import pytest

from adapters import FailureKind, InvocationRequest, InvocationResult, ProviderSpec, ResultState, get_adapter
from adapters.runtime import BaseRuntimeAdapter, CredentialStrategy, Credentials, RuntimeAdapter, RuntimeKind, RuntimeProfile
from app.connect.runtimes import cli_runtime, http_runtime, managed_only_runtime, mcp_runtime, rank, select
from app.services import providers as provider_service
from app.services import runtime as runtime_service


def http(rid, **kw):
    defaults = dict(kind=RuntimeKind.HTTP, adapter={"kind": "http", "config": {"base_url": "http://x", "invoke": {"path": "/ask"}}}, display_name="Connected over the network", availability="ready", confidence="high", credentials=Credentials(strategy=CredentialStrategy.RUNTIME_MANAGED), evidence=[])
    defaults.update(kw)
    return http_runtime(rid, **defaults)


def cli(rid, **kw):
    defaults = dict(kind=RuntimeKind.PYTHON_ENTRYPOINT, adapter={"kind": "command", "config": {"argv": ["python", "-m", "x"], "input": {"mode": "flag", "flag": "--q"}}}, display_name="Runs from this project", availability="needs_worker", confidence="high", credentials=Credentials(strategy=CredentialStrategy.BEVRO_MANAGED, names=["KEY"], required_from_user=True), evidence=[])
    defaults.update(kw)
    return cli_runtime(rid, **defaults)


def test_public_view_hides_mechanism_and_paths():
    rt = cli("cli", target="/home/someone/agents/x")
    public = rt.public()
    assert public["display_name"] == "Runs from this project" and public["credentials"]["label"] == "Missing"
    assert "adapter" not in public and "target" not in public and "kind" not in public
    assert "/home/" not in str(public)
    advanced = rt.advanced()
    assert advanced["kind"] == "python_entrypoint" and advanced["adapter"] == "command" and "/home/" not in str(advanced)


def test_ranking_prefers_running_then_managed_then_declared_then_inferred():
    running = http("running", kind=RuntimeKind.PROCESS, display_name="Already running on this machine")
    compose = http("compose", kind=RuntimeKind.DOCKER_COMPOSE, availability="needs_start", confidence="medium")
    mcp = mcp_runtime("mcp", stdio=True, adapter={"kind": "mcp", "config": {"argv": ["python", "-m", "s"], "tool": {"name": "ask", "argument": "q"}}}, display_name="Uses MCP on this machine", availability="needs_worker", confidence="high", credentials=Credentials(strategy=CredentialStrategy.NONE), evidence=[])
    declared = cli("cli", confidence="high", credentials=Credentials(strategy=CredentialStrategy.INHERITED_ENVIRONMENT, names=["KEY"]))
    inferred = cli("cli-2", confidence="low")
    ranked = rank([inferred, declared, compose, mcp, running])
    assert [rt.id for rt in ranked] == ["running", "mcp", "cli", "compose", "cli-2"]
    active, choice = select([inferred, declared, compose, mcp, running])
    assert active == "running" and choice is False


def test_close_call_between_different_mechanisms_asks_the_person():
    declared = cli("cli", credentials=Credentials(strategy=CredentialStrategy.INHERITED_ENVIRONMENT, names=["KEY"]))
    mcp = mcp_runtime("mcp", stdio=True, adapter={"kind": "mcp", "config": {"argv": ["python", "-m", "s"], "tool": {"name": "ask", "argument": "q"}}}, display_name="Uses MCP on this machine", availability="needs_worker", confidence="medium", credentials=Credentials(strategy=CredentialStrategy.NONE), evidence=[])
    active, choice = select([declared, mcp])
    assert active in ("mcp", "cli") and choice is True
    # Two profiles of the same mechanism never need a choice.
    assert select([cli("a"), cli("b", confidence="medium")]) == ("a", False)


def test_non_invocable_runtimes_are_never_selected_but_stay_on_record():
    unit = managed_only_runtime("systemd", kind=RuntimeKind.SYSTEMD, display_name="Runs as a local service (systemd)", evidence=["unit"], note="Scheduled job", credentials=Credentials(strategy=CredentialStrategy.SYSTEMD_ENVIRONMENT, names=["KEY"]))
    active, choice = select([unit, cli("cli")])
    assert active == "cli" and not choice
    assert select([unit]) == ("systemd", False) and not unit.invocable


def test_adapters_implement_the_runtime_contract():
    for kind in ("http", "mcp", "command", "local", "claude_code"):
        adapter = get_adapter(kind)
        assert isinstance(adapter, RuntimeAdapter), kind
        assert isinstance(adapter, BaseRuntimeAdapter), kind
        for method in ("health", "invoke", "status", "cancel", "collect_artifacts"):
            assert callable(getattr(adapter, method)), (kind, method)


def test_existing_records_migrate_to_profiles(seeded):
    for provider in provider_service.list_providers(seeded):
        assert provider.runtimes and provider.active_runtime
    research = provider_service.get_by_slug(seeded, "research")
    claude = provider_service.get_by_slug(seeded, "claude-code")
    assert runtime_service.active_runtime(research).kind == "builtin"
    rt = runtime_service.active_runtime(claude)
    assert rt.kind == "cli" and rt.abilities.background and rt.credentials.strategy == "runtime_managed"
    old = provider_service.register_provider(seeded, {"name": "Old HTTP", "adapter": {"kind": "http", "method": "api", "config": {"base_url": "http://old.local", "auth": {"type": "bearer", "secret": "api_key"}}}})
    rt = runtime_service.active_runtime(old)
    assert rt.kind == "http" and rt.credentials.names == ["api_key"] and rt.credentials.strategy == "bevro_managed"
    assert old.adapter["kind"] == "http" and old.adapter["config"]["base_url"] == "http://old.local"  # the block is untouched
    old_cmd = provider_service.register_provider(seeded, {"name": "Old CLI", "adapter": {"kind": "command", "config": {"argv": ["python", "-m", "x"], "cwd": "/srv/x", "secret_env": ["OPENAI_API_KEY"], "self_configured": ["OPENAI_API_KEY"], "input": {"mode": "stdin"}}}})
    rt = runtime_service.active_runtime(old_cmd)
    assert rt.kind == "cli" and rt.input == "stdin" and rt.credentials.strategy == "project_dotenv"


def test_execution_goes_through_the_active_runtime(seeded):
    research = provider_service.get_by_slug(seeded, "research")
    result, artifacts = runtime_service.execute(research, InvocationRequest(task_id="t", run_id="r", request="Find three options"))
    assert result.state == ResultState.COMPLETED and artifacts
    broken = provider_service.register_provider(seeded, {"name": "Broken", "adapter": {"kind": "nope"}})
    result, artifacts = runtime_service.execute(broken, InvocationRequest(task_id="t", run_id="r", request="x"))
    assert result.state == ResultState.FAILED and result.failure == FailureKind.CONFIGURATION_PROBLEM and artifacts == []


def test_failure_kinds_cover_every_native_error_class():
    assert {k.value for k in FailureKind} >= {"configuration_problem", "credential_required", "provider_unavailable", "invocation_failed", "timed_out", "cancelled", "output_invalid"}
