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

# Keep common actions typed. Administrative credentials, installation and
# automatic outcome acceptance are deliberately absent from this local surface.
TOOLS.update(
    {
        "awb_usage": (
            "Read provider-reported token usage and reserved capacity; unavailable costs stay unknown",
            "fleet.usage",
            {
                "project": {"type": "string"},
                "request_id": {"type": "string"},
                "revision": {"type": "integer"},
            },
            [],
        ),
        "awb_projects": (
            "List project identities before routing",
            "project.list",
            {},
            [],
        ),
        "awb_request": (
            "Read current requirements and revision",
            "request.get",
            {"request_id": {"type": "string"}},
            ["request_id"],
        ),
        "awb_plan": (
            "Compile a bounded team plan using qualified profiles; does not dispatch it",
            "request.plan",
            {
                "request_id": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["simple", "code", "design", "report", "research"],
                },
                "model_override": {"type": "string"},
            },
            ["request_id"],
        ),
        "awb_capabilities": (
            "Inspect installed and enabled integrations without implying qualification",
            "capabilities.list",
            {},
            [],
        ),
        "awb_backend_recall": (
            "Search an explicitly configured project memory backend; derived memories remain unverified",
            "backend.recall",
            {
                "backend": {"type": "string", "enum": ["hindsight", "openviking"]},
                "project": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            ["backend", "project", "query"],
        ),
        "awb_backend_read": (
            "Read an original through a managed backend source binding",
            "backend.read",
            {
                "backend": {"type": "string", "enum": ["hindsight", "openviking"]},
                "project": {"type": "string"},
                "source_id": {"type": "string"},
            },
            ["backend", "project", "source_id"],
        ),
        "awb_backend_ingest": (
            "Ingest an archived project source into an explicitly write-enabled backend",
            "backend.ingest",
            {
                "backend": {"type": "string", "enum": ["hindsight", "openviking"]},
                "project": {"type": "string"},
                "source_id": {"type": "string"},
            },
            ["backend", "project", "source_id"],
        ),
        "awb_execution_workspace": (
            "Create an owned project workspace for code and tests",
            "execution.create_workspace",
            {"project": {"type": "string"}},
            ["project"],
        ),
        "awb_execution_plan": (
            "Prepare exact compiler/test commands and their hash for review",
            "execution.plan",
            {
                "workspace_id": {"type": "string"},
                "language": {"type": "string"},
                "goal": {"type": "string"},
                "allow_package_scripts": {"type": "boolean"},
            },
            ["workspace_id"],
        ),
        "awb_execution_run": (
            "Run a reviewed command plan only when its expected hash still matches",
            "execution.run",
            {
                "plan_id": {"type": "string"},
                "expected_sha256": {"type": "string"},
                "timeout": {"type": "integer"},
                "max_output_bytes": {"type": "integer"},
                "memory_mb": {"type": "integer"},
            },
            ["plan_id", "expected_sha256"],
        ),
        "awb_execution_status": (
            "Read actual execution receipts and bounded logs",
            "execution.get_run",
            {"run_id": {"type": "string"}},
            ["run_id"],
        ),
        "awb_artifact_verify": (
            "Recheck artifact paths and hashes; does not certify behavior",
            "execution.verify_artifacts",
            {"manifest_id": {"type": "string"}},
            ["manifest_id"],
        ),
        "awb_recover": (
            "Recover a checkpoint against current requirements and artifact hashes",
            "continuation.recover",
            {"request_id": {"type": "string"}},
            ["request_id"],
        ),
        "awb_rollover": (
            "Request a bounded fresh session after checkpointing; successor waits for stop confirmation",
            "lifecycle.request",
            {"request_id": {"type": "string"}, "node": {"type": "string"}},
            ["request_id", "node"],
        ),
        "awb_writing_check": (
            "Check length and vague wording without claiming factual correctness",
            "writing.lint",
            {"text": {"type": "string"}, "max_words": {"type": "integer"}},
            ["text"],
        ),
    }
)


def validate_arguments(args, properties, required):
    if (
        not isinstance(args, dict)
        or set(args) - set(properties)
        or set(required) - set(args)
    ):
        raise WorkbenchError("Invalid tool arguments")
    types = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, value in args.items():
        rule = properties[key]
        if type(value) is not types[rule["type"]]:
            raise WorkbenchError("Invalid argument type: " + key)
        if "enum" in rule and value not in rule["enum"]:
            raise WorkbenchError("Unsupported argument value: " + key)


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
                validate_arguments(args, spec[2], spec[3])
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
