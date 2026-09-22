"""MCP over stdio: the fake server in tests/fake_mcp_server.py, started only on request."""

import sys
from pathlib import Path

from adapters import InvocationContext, InvocationRequest, ProviderSpec, get_adapter
from adapters.mcp_client import StdioSession, text_of
from adapters.registry import execution_mode
from app.connect.draft import DraftAuth, ProviderDraft
from app.connect.strategies.mcp import choose_tool, refine_stdio

SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def test_choose_tool_prefers_a_plain_string_tool_and_skips_structured_ones():
    tools = [
        {"name": "delete_all", "inputSchema": {"type": "object", "properties": {"confirm": {"type": "boolean"}}, "required": ["confirm"]}},
        {"name": "lookup", "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}, "q": {"type": "string"}}, "required": ["id"]}},
        {"name": "ask_notes", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    ]
    assert choose_tool(tools) == ("ask_notes", "question")
    assert choose_tool(tools[:2]) is None


def test_stdio_session_initialises_lists_and_calls():
    with StdioSession([sys.executable, SERVER], timeout=10) as session:
        session.initialize()
        assert session.server_info["name"] == "fixture-notes"
        names = [t["name"] for t in session.list_tools()]
        assert names == ["ask", "delete_all"]
        assert text_of(session.call_tool("ask", {"query": "lunch"})).startswith("You asked: lunch")


def test_refine_stdio_reads_tools_when_the_person_asks_for_a_test(monkeypatch, tmp_path):
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(tmp_path))
    draft = ProviderDraft(name="Notes", mechanism="mcp", adapter={"kind": "mcp", "config": {"argv": [sys.executable, SERVER], "cwd": str(tmp_path), "secret_env": [], "tool": {}}}, availability="needs_worker", confidence="medium", auth=DraftAuth())
    refined = refine_stdio(draft, {})
    assert [c.id for c in refined.capabilities] == ["ask", "delete_all"]
    assert refined.adapter["config"]["tool"] == {"name": "ask", "argument": "query"}
    assert refined.adapter["config"]["cwd"] == str(tmp_path)  # paths kept, never shown
    assert refined.confidence == "high" and refined.invocable


def test_mcp_stdio_provider_runs_in_the_background_and_answers(monkeypatch, tmp_path):
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(tmp_path))
    adapter = get_adapter("mcp")
    block = {"kind": "mcp", "config": {"argv": [sys.executable, SERVER], "cwd": str(tmp_path), "tool": {"name": "ask", "argument": "query"}}}
    assert execution_mode(adapter, block) == "background"
    spec = ProviderSpec(id="1", slug="notes", name="Notes", adapter=block)
    assert adapter.check(spec, {}).ok
    phases: list[str] = []
    result = adapter.invoke(spec, InvocationRequest(task_id="t", run_id="r", request="What did I note about lunch?"), InvocationContext(progress=phases.append))
    assert result.state == "completed" and result.summary == "You asked: What did I note about lunch?"
    assert result.artifacts[0].type == "report" and "Here is what I found" in result.artifacts[0].payload["text"]
    assert phases == ["Asking Notes"]


def test_mcp_stdio_refuses_a_folder_outside_the_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("BEVRO_LOCAL_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    spec = ProviderSpec(id="1", slug="notes", name="Notes", adapter={"kind": "mcp", "config": {"argv": [sys.executable, SERVER], "cwd": str(tmp_path), "tool": {"name": "ask", "argument": "query"}}})
    health = get_adapter("mcp").check(spec, {})
    assert not health.ok and "outside the approved" in (health.detail or "")
