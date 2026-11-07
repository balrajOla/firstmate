"""Minimal MCP stdio transport exposing typed Workbench actions to local harnesses."""

import json
import sys
from .app import Application
from .util import WorkbenchError

TOOLS = {
    "awb_submit": (
        "Save a prompt before routing or execution",
        "prompt.submit",
        {
            "text": {"type": "string"},
            "client_key": {"type": "string"},
            "project": {"type": "string"},
        },
        ["text", "client_key"],
    ),
    "awb_search": (
        "Retrieve project-scoped original evidence",
        "context.search",
        {"project": {"type": "string"}, "query": {"type": "string"}},
        ["project", "query"],
    ),
    "awb_packet": (
        "Assemble a bounded source-linked context packet",
        "context.packet",
        {
            "project": {"type": "string"},
            "query": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        ["project", "query"],
    ),
    "awb_outcomes": (
        "Read persistent outcomes separately from logs",
        "outcome.list",
        {"project": {"type": "string"}},
        [],
    ),
    "awb_checkpoint": (
        "Commit a fresh-context handoff with cumulative budget",
        "continuation.checkpoint",
        {
            "request_id": {"type": "string"},
            "revision": {"type": "integer"},
            "generation": {"type": "integer"},
            "summary": {"type": "string"},
            "artifacts": {"type": "array", "items": {"type": "object"}},
            "pending": {"type": "array", "items": {"type": "string"}},
        },
        ["request_id", "revision", "generation", "summary", "artifacts", "pending"],
    ),
}


def serve_stdio(home, source=sys.stdin, sink=sys.stdout):
    app = Application(home)
    for line in source:
        if len(line) > 2 * 1024 * 1024:
            continue
        request = {}
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise WorkbenchError("JSON-RPC request must be an object")
            request = value
            if "id" not in request:
                continue
            method = request["method"]
            if method == "initialize":
                requested = request.get("params", {}).get("protocolVersion")
                supported = {"2024-11-05", "2025-03-26", "2025-06-18"}
                result = {
                    "protocolVersion": (
                        requested if requested in supported else "2025-06-18"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "assistant-workbench", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": name,
                            "description": spec[0],
                            "inputSchema": {
                                "type": "object",
                                "properties": spec[2],
                                "required": spec[3],
                                "additionalProperties": False,
                            },
                        }
                        for name, spec in TOOLS.items()
                    ]
                }
            elif method == "tools/call":
                name = request["params"]["name"]
                args = request["params"].get("arguments", {})
                spec = TOOLS[name]
                if set(args) - set(spec[2]) or set(spec[3]) - set(args):
                    raise WorkbenchError("Invalid tool arguments")
                value = app.dispatch(spec[1], args)
                result = {
                    "content": [{"type": "text", "text": json.dumps(value)}],
                    "isError": False,
                }
            else:
                raise WorkbenchError("Unsupported MCP method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32602, "message": str(exc)},
            }
        sink.write(json.dumps(response) + "\n")
        sink.flush()
