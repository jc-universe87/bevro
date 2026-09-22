"""A tiny MCP server on stdin/stdout for tests: one tool, `ask`, that echoes."""

import json
import sys

TOOLS = [
    {"name": "ask", "description": "Ask the notes assistant a question", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "delete_all", "description": "Dangerous", "inputSchema": {"type": "object", "properties": {"confirm": {"type": "boolean"}}, "required": ["confirm"]}},
]


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, req_id = msg.get("method"), msg.get("id")
        if req_id is None:
            continue  # notification
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture-notes", "version": "1.0"}, "instructions": "Answers questions about your notes."}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            args = msg["params"].get("arguments") or {}
            result = {"content": [{"type": "text", "text": f"You asked: {args.get('query')}\n\nHere is what I found."}], "isError": False}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "unknown method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}), flush=True)


if __name__ == "__main__":
    main()
