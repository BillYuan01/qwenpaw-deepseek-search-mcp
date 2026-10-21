#!/usr/bin/env python3
"""DeepSeek 官方 Search / Fetch API 的 MCP 服务器（stdio）。

文档：https://api-docs.deepseek.com/guides/search
计费：5 元 / 1000 次成功请求（约 ¥0.005/次），search 与 fetch 同价。

环境变量：
  DEEPSEEK_API_KEY       必填。DeepSeek 平台签发的 API Key。
  DEEPSEEK_API_KEY_FILE  可选。key 存在文件里时用它指定路径（读首个非空行）。
  DEEPSEEK_BASE_URL      可选。默认 https://api.deepseek.com
  DEEPSEEK_TIMEOUT       可选。单次请求超时秒数，默认 60。

说明：
  * 只依赖 Python 标准库 + mcp，不需要 requests。
  * 所有输出走 MCP 协议（stdout），日志请勿打印到 stdout，否则会破坏 jsonrpc 流。
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "60"))

mcp = FastMCP("deepseek-search")


def _api_key() -> str:
    """按 环境变量 → key 文件 的顺序取 key，取不到返回空串。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key

    path = os.environ.get("DEEPSEEK_API_KEY_FILE", "").strip()
    if path:
        try:
            with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        return line
        except OSError:
            pass
    return ""


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST 到 DeepSeek 工具 API，错误统一转成 {"error": ..., "detail": ...}。"""
    key = _api_key()
    if not key:
        return {
            "error": "缺少 DEEPSEEK_API_KEY",
            "detail": "请在 MCP 配置的 env 里填 DEEPSEEK_API_KEY，或用 DEEPSEEK_API_KEY_FILE 指向存放 key 的文件。",
        }

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # 保留服务端原始错误，便于区分 401（key 无效）/ 402（余额不足）/ 403 / 429（限流）
        body = exc.read().decode("utf-8", "replace")
        try:
            detail: Any = json.loads(body)
        except Exception:
            detail = body[:500]
        return {"error": f"HTTP {exc.code}", "detail": detail}
    except Exception as exc:  # 网络错误 / 超时
        return {"error": f"{type(exc).__name__}: {exc}"}


@mcp.tool()
def deepseek_search(
    query: str,
    count: int = 5,
    need_content: bool = False,
    ori_question: str = "",
    lang: str = "",
) -> str:
    """用 DeepSeek 自建搜索引擎检索网页。

    Args:
        query: 搜索词，1-128 字符。支持 site:/ -site:/ -term/ "精确短语" / lang:/ after:/ before: 等操作符。
        count: 返回条数，1-50，默认 5。
        need_content: 是否返回整页正文（content 字段），默认 False，仅返回 snippet/summary。
        ori_question: 触发本次搜索的用户原始问题，用于改善检索理解，可留空。
        lang: 便捷参数，会以 lang:<值> 追加到 query，例如 zh、en。
    """
    q = query.strip()
    if lang.strip():
        q = f"{q} lang:{lang.strip()}"

    payload: dict[str, Any] = {
        "query": q,
        "count": max(1, min(int(count), 50)),
        "need_content": bool(need_content),
    }
    if ori_question.strip():
        payload["ori_question"] = ori_question.strip()[:128]

    data = _post("/v1/tools/search", payload)
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    # 只回传对模型有用的字段，避免把上游整包塞进上下文
    slim = [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "summary": r.get("summary"),
            "snippet": r.get("snippet"),
            "site_name": r.get("site_name"),
            "date": r.get("date"),
            "authority": r.get("authority"),
            "rerank_score": r.get("rerank_score"),
            **({"content": r.get("content")} if need_content else {}),
        }
        for r in data.get("results", [])
    ]
    return json.dumps({"results": slim}, ensure_ascii=False)


@mcp.tool()
def deepseek_fetch(url: str) -> str:
    """抓取单个网页并用 DeepSeek 转成 Markdown 正文。

    Args:
        url: 必须是 http/https 的完整 URL，不含用户名密码，长度 ≤ 8192。
    """
    data = _post("/v1/tools/fetch", {"url": url.strip()})
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(
        {"title": data.get("title", ""), "content": data.get("content", "")},
        ensure_ascii=False,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
