#!/usr/bin/env python3
"""冒烟测试：用官方 MCP 客户端直连本服务器，验证工具注册与调用链路。

用法：
    export DEEPSEEK_API_KEY=sk-xxxxxxxx
    python test_client.py

没有 key 时只做 list_tools；有 key 时额外跑一次 search 和一次 fetch（各计费一次）。
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))

PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(HERE, "server.py")],
    env=dict(os.environ),
)


async def main() -> int:
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            assert names == ["deepseek_search", "deepseek_fetch"], f"工具注册异常: {names}"

            if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
                print("未设置 DEEPSEEK_API_KEY，跳过联网调用。")
                return 0

            res = await session.call_tool(
                "deepseek_search", {"query": "Model Context Protocol", "count": 3}
            )
            payload = json.loads(res.content[0].text)
            assert "results" in payload, payload
            print(f"search ok: {len(payload['results'])} results")

            res = await session.call_tool("deepseek_fetch", {"url": "https://example.com"})
            payload = json.loads(res.content[0].text)
            assert payload.get("content"), payload
            print(f"fetch ok: {payload.get('title') or 'Example Domain'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
