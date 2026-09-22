#!/usr/bin/env python3
"""A stand-in for the `claude` CLI used by the automated tests.

Speaks the same stream-json shape as Claude Code 2.1.278 in print mode and
behaves according to markers in the prompt:

  (default)      reads, edits a file, "runs checks", finishes with a Summary line
  SLOW           keeps running until killed (cancellation tests)
  CRASH          exits 2 without a result event
  ERROR_RESULT   emits a result event with is_error = true
  MAX_TURNS      emits result subtype error_max_turns
  NOISY          result text full of terminal escape codes
  NO_CHANGES     touches nothing
  TOUCH_DIRTY    also modifies dirty.txt if it exists (attribution tests)

argv is written to $FAKE_CLAUDE_ARGV_FILE when set, so tests can check flags.
"""

import json
import os
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("9.9.9 (fake Claude Code)")
        return 0
    if os.environ.get("FAKE_CLAUDE_ARGV_FILE"):
        with open(os.environ["FAKE_CLAUDE_ARGV_FILE"], "w") as fh:
            json.dump(argv, fh)
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    emit({"type": "system", "subtype": "init", "session_id": "fake-session-1", "cwd": os.getcwd(), "tools": ["Read", "Edit", "Bash"], "model": "fake"})

    if "SLOW" in prompt:
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}})
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "x"}}]}})
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}})
        for _ in range(600):
            time.sleep(0.1)
        return 0
    if "CRASH" in prompt:
        sys.stderr.write("\x1b[31msomething exploded\x1b[0m\nTraceback (most recent call last): ...\n")
        return 2

    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look."}, {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}}]}})
    if "NO_CHANGES" not in prompt:
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "app.py"}}]}})
        with open("app.py", "a") as fh:
            fh.write("\n# changed by fake claude\n")
        with open("new_module.py", "w") as fh:
            fh.write("def hello():\n    return 'hello'\n")
        if "TOUCH_DIRTY" in prompt and os.path.exists("dirty.txt"):
            with open("dirty.txt", "a") as fh:
                fh.write("also touched by the run\n")
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": "2 passed"}]}})

    if "ERROR_RESULT" in prompt:
        emit({"type": "result", "subtype": "error", "is_error": True, "result": "API error: \x1b[31mboom\x1b[0m", "session_id": "fake-session-1", "num_turns": 3, "total_cost_usd": 0.01, "duration_ms": 10})
        return 1
    if "MAX_TURNS" in prompt:
        emit({"type": "result", "subtype": "error_max_turns", "is_error": False, "result": "", "session_id": "fake-session-1", "num_turns": 40, "total_cost_usd": 0.5, "duration_ms": 10})
        return 0
    text = "I added new_module.py and a comment in app.py, then ran the tests.\n\nSummary: Added a hello module and the tests pass"
    if "NOISY" in prompt:
        text = "\x1b[1mBold\x1b[0m output\x07 with\x1b]0;title\x07 noise.\n\nSummary: \x1b[32mAll good\x1b[0m"
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "fake-session-1", "num_turns": 4, "total_cost_usd": 0.12, "duration_ms": 1234, "permission_denials": []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
