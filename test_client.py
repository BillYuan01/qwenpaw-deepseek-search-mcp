#!/usr/bin/env python3
"""冒烟测试：用官方 MCP 客户端直连本服务器，验证工具注册与调用链路。

用法：
    python test_client.py

不要求设置 DEEPSEEK_API_KEY：会复用 server.py 的四级解析逻辑，
已配好 QwenPaw DeepSeek 提供商的机器可以直接跑通。
一次完整运行会消耗 2 次 API 调用（search + fetch），约 ¥0.01。
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import server  # noqa: E402  —— 复用同一套 key 解析逻辑，避免两处实现漂移

PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(HERE, "server.py")],
    env=dict(os.environ),
)


async def main() -> int:
    key, source = server.resolve_api_key()
    print(f"key 来源: {source or '无'}（长度 {len(key)}）")

    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("tools:", names)
            assert names == ["deepseek_fetch", "deepseek_search"], f"工具注册异常: {names}"

            if not key:
                print("没有可用 key，跳过联网调用。")
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
