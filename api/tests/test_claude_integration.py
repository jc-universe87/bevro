"""Manual integration test against the real, locally installed Claude Code CLI.

Skipped unless BEVRO_RUN_CLAUDE_INTEGRATION=1. It spends real quota (one small
task) and needs the CLI on PATH and logged in. Run it from the host:

    BEVRO_RUN_CLAUDE_INTEGRATION=1 BEVRO_TEST_DATABASE_URL=postgresql+psycopg://bevro:bevro@localhost:6142/bevro_test \
      .venv/bin/python -m pytest api/tests/test_claude_integration.py -q -s
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from adapters import InvocationContext, InvocationRequest, ProviderSpec, ResultState, get_adapter

pytestmark = pytest.mark.skipif(os.environ.get("BEVRO_RUN_CLAUDE_INTEGRATION") != "1", reason="set BEVRO_RUN_CLAUDE_INTEGRATION=1 to run against the real CLI")


def test_real_claude_creates_a_file_in_a_scratch_repo(tmp_path: Path):
    assert shutil.which("claude"), "claude CLI not on PATH"
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# scratch\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    spec = ProviderSpec(id="x", slug="claude-code", name="Claude Code", adapter={"kind": "claude_code", "config": {"max_turns": 8, "max_budget_usd": 0.5}})
    request = InvocationRequest(
        task_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()),
        request="Create a file called hello.txt containing exactly the line 'hello from bevro'. Do nothing else.",
        input={"workspace": {"id": "w", "name": "Scratch", "path": str(tmp_path), "permissions": ["read", "write"]}, "permissions": ["read", "write"]},
    )
    steps: list[str] = []
    result = get_adapter("claude_code").invoke(spec, request, InvocationContext(progress=steps.append, log_dir=str(tmp_path / "_logs")))
    print("\nsteps:", steps, "\nsummary:", result.summary, "\nerror:", result.error, "\nmeta:", {k: result.metadata.get(k) for k in ("cost_usd", "num_turns", "exit_code")})
    assert result.state == ResultState.COMPLETED, result.error
    assert (tmp_path / "hello.txt").read_text().strip() == "hello from bevro"
    changed = next(a for a in result.artifacts if a.type == "structured")
    assert ["hello.txt", "Created"] in changed.payload["rows"]
