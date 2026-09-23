"""Classifying what the person typed: pure text, nothing touched."""

import pytest

from app.connect.targets import TargetError, classify_target, split_command


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("https://agent.example.com", "url"),
        ("http://localhost:8000", "url"),
        ("http://localhost:3333/mcp", "mcp"),
        ("https://tools.example.com/sse", "mcp"),
        ("~/agents/market-research", "local"),
        ("/srv/agents/thing", "local"),
        ("./relative/project", "local"),
        ("market-research", "local"),
        ("agents/market-research", "local"),
        ("python -m market_research.agent", "command"),
        ("npx my-agent --verbose", "command"),
        ('"/home/me/my agents/x"', "local"),
    ],
)
def test_url_path_mcp_and_command_classification(text, kind):
    assert classify_target(text).kind == kind


def test_commands_are_split_without_a_shell():
    target = classify_target('python -m my_agent --mode "fast lane"')
    assert target.argv == ("python", "-m", "my_agent", "--mode", "fast lane")


@pytest.mark.parametrize(
    "text",
    [
        "python -m x; rm -rf /",
        "python -m x | cat /etc/passwd",
        "npx agent && curl evil",
        "python -m x > /tmp/out",
        "python $(whoami)",
        "python `id`",
        "python -m x\nrm -rf /",
    ],
)
def test_command_injection_is_rejected(text):
    with pytest.raises(TargetError):
        classify_target(text)


def test_unbalanced_quotes_and_empty_input_are_refused():
    with pytest.raises(TargetError):
        split_command('python -m "x')
    with pytest.raises(TargetError):
        classify_target("   ")
    with pytest.raises(TargetError):
        classify_target("x" * 3000)


def test_labels_never_carry_an_absolute_path():
    assert classify_target("/home/someone/agents/market-research/").label == "market-research"
    assert classify_target("~/agents/x").label == "x"
    assert classify_target("https://a.example/mcp").label == "https://a.example/mcp"
