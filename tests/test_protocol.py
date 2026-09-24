from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.asyncio
async def test_stdio_server_lists_bounded_android_tools() -> None:
    project = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command="uv",
        args=["run", "jev-android-mcp"],
        cwd=project,
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()

    assert initialized.server_info.name == "Jev Android MCP"
    assert {
        "device_list",
        "android_observe",
        "android_act",
        "android_run",
        "android_reset",
    }.issubset({tool.name for tool in tools.tools})
    android_run = next(tool for tool in tools.tools if tool.name == "android_run")
    schema = android_run.model_dump(by_alias=True)["inputSchema"]
    assert schema["properties"]["backend"]["enum"] == [
        "auto",
        "typesafe",
        "openrouter",
    ]
