from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"clean": {"type": "boolean"}},
    "required": ["clean"],
    "additionalProperties": False,
}

server = Server("mini-code-agent-test", version="1.0.0")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    schema = dict(INPUT_SCHEMA)
    if "--drift-schema" in sys.argv:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        }
    tools = [
        types.Tool(
            name="status",
            description="Untrusted remote description.",
            inputSchema=schema,
            outputSchema=OUTPUT_SCHEMA,
        )
    ]
    if "--extra-tool" in sys.argv:
        tools.append(
            types.Tool(
                name="unexpected",
                description="An unapproved Tool.",
                inputSchema={"type": "object", "additionalProperties": False},
            )
        )
    return tools


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name != "status":
        raise ValueError("Unknown fixture Tool.")
    call_log = os.environ.get("MCP_TEST_CALL_LOG")
    if call_log:
        with Path(call_log).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    {"name": name, "path": arguments.get("path")},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
    return {"clean": True}


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
