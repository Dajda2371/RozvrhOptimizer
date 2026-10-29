"""Spawn the server over stdio like an MCP client would and call a few tools.

Usage: uv run python scripts/e2e_check.py   (reads BAKALARI_* from the environment / .env)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def show(label: str, result) -> None:
    if getattr(result, "is_error", False):
        print(f"{label}: TOOL ERROR -> {result.content[0].text if result.content else result}")
        return
    payload = getattr(result, "structured_content", None)
    if payload is None and result.content:
        payload = result.content[0].text
    text = json.dumps(payload, ensure_ascii=False)
    print(f"{label}: {text[:400]}{'…' if len(text) > 400 else ''}")


async def main() -> None:
    load_dotenv()
    env = {k: v for k, v in os.environ.items() if k.startswith("BAKALARI_") or k in ("PATH", "HOME")}
    params = StdioServerParameters(command="uv", args=["run", "rozvrh-mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])
            show("get_server_info", await session.call_tool("get_server_info"))
            show("get_daily_changes", await session.call_tool("get_daily_changes"))
            show("list_classes", await session.call_tool("list_classes"))
            show("get_class_timetable", await session.call_tool("get_class_timetable", {"class_id": "1.A"}))
            show("find_free_rooms", await session.call_tool("find_free_rooms", {"day": "po", "period": 1, "rooms": ["1101", "2507"]}))
            show("bad day", await session.call_tool("find_free_rooms", {"day": "xx", "period": 1}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
