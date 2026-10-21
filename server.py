#!/usr/bin/env python3
"""DeepSeek 官方 Search / Fetch API 的 MCP 服务器（stdio）。

文档：https://api-docs.deepseek.com/guides/search
计费：5 元 / 1000 次成功请求（约 ¥0.005/次），search 与 fetch 同价。

API Key 解析顺序（先命中先返回）：
  1. 环境变量 DEEPSEEK_API_KEY
  2. 环境变量 DEEPSEEK_API_KEY_FILE 指向的文件（取首个非空行）
  3. QwenPaw 内置 DeepSeek 提供商的 secret 文件
     （默认 ~/.qwenpaw.secret/providers/builtin/deepseek.json，可用 QWENPAW_KEY_FILE 覆盖）
     —— 只在装了 QwenPaw 且已配置 DeepSeek 提供商时有效，其他客户端会跳过这一级。

环境变量：
  DEEPSEEK_API_KEY        DeepSeek API Key（最高优先级）
  DEEPSEEK_API_KEY_FILE   key 文件路径（读首个非空行）
  DEEPSEEK_BASE_URL       可选，默认 https://api.deepseek.com
  DEEPSEEK_TIMEOUT        可选，单次请求超时秒数，默认 60
  QWENPAW_KEY_FILE        可选，覆盖 QwenPaw provider secret 路径

命令行自检：
  python server.py --check-key     # 打印命中的 key 来源与长度，不打印 key 本身

注意：stdio 模式下 stdout 就是 jsonrpc 通道，任何调试输出都必须走 stderr。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "60"))

QWENPAW_KEY_FILE = os.environ.get(
    "QWENPAW_KEY_FILE",
    os.path.expanduser("~/.qwenpaw.secret/providers/builtin/deepseek.json"),
)

mcp = FastMCP("deepseek-search")


def _strip(value: Any) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _decrypt_if_needed(value: str) -> str:
    """QwenPaw 把 secret 存成 ``ENC:`` 前缀的密文，直接拿去请求会 401。

    只有装了 QwenPaw 才解得了；解不了就返回空串，由调用方继续尝试下一级来源。
    """
    value = _strip(value)
    if not value.startswith("ENC:"):
        return value  # 明文（或别人的加密串）原样返回
    try:
        from qwenpaw.security.secret_store import decrypt
    except Exception:
        return ""  # 没装 QwenPaw，跳过这一级
    try:
        return _strip(decrypt(value))
    except Exception:
        return ""  # 缺 master key / 版本不匹配


def _from_file(path: str) -> str:
    """读文件里的首个非空行，失败返回空串。"""
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return line
    except OSError:
        pass
    return ""


def _from_qwenpaw_provider() -> str:
    """读 QwenPaw 内置 DeepSeek 提供商的 api_key 字段。"""
    try:
        with open(QWENPAW_KEY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return ""
    return _decrypt_if_needed(_strip(data.get("api_key")))


def resolve_api_key() -> tuple[str, str]:
    """按优先级返回 (key, 来源标签)；全部失败时返回 ("", "")。"""
    key = _strip(os.environ.get("DEEPSEEK_API_KEY"))
    if key:
        return _decrypt_if_needed(key), "env:DEEPSEEK_API_KEY"

    path = _strip(os.environ.get("DEEPSEEK_API_KEY_FILE"))
    if path:
        key = _from_file(path)
        if key:
            return _decrypt_if_needed(key), "file:DEEPSEEK_API_KEY_FILE"

    key = _from_qwenpaw_provider()
    if key:
        return key, "qwenpaw:provider"

    return "", ""


def _api_key() -> str:
    return resolve_api_key()[0]


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST 到 DeepSeek 工具 API；错误统一转成 {"error": ..., "detail": ...}。"""
    key = _api_key()
    if not key:
        return {
            "error": "缺少 DEEPSEEK_API_KEY",
            "detail": (
                "已依次尝试：环境变量 DEEPSEEK_API_KEY → DEEPSEEK_API_KEY_FILE → "
                f"QwenPaw provider secret（{QWENPAW_KEY_FILE}）。"
                "请在 MCP 配置的 env 里填 DEEPSEEK_API_KEY，或先配好 QwenPaw 的 DeepSeek 提供商。"
            ),
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
    if "--check-key" in sys.argv[1:]:
        key, source = resolve_api_key()
        if key:
            print(f"OK   来源: {source}   长度: {len(key)}")
            print("（不会打印 key 本身）")
            return
        print("MISSING  已尝试：DEEPSEEK_API_KEY → DEEPSEEK_API_KEY_FILE → QwenPaw provider")
        print(f"QwenPaw secret 路径：{QWENPAW_KEY_FILE}")
        raise SystemExit(1)

    mcp.run()


if __name__ == "__main__":
    main()
