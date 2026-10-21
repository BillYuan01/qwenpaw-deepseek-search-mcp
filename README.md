# deepseek-search-mcp

把 **DeepSeek 官方 Search / Fetch API** 包成一个 MCP 服务器（stdio），给 Claude Desktop、Cursor、Cline、QwenPaw 等任意 MCP 客户端用。

两个工具：

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `deepseek_search` | 联网检索，返回标题 / URL / 摘要 / 站点 / 时间 / 权威度 | 让模型能查到训练数据之后的事 |
| `deepseek_fetch` | 抓单个网页，转成干净的 Markdown 正文 | 把搜索结果读成全文，再总结或入库 |

不维护索引、不写爬虫：检索、清洗、正文提取都在 DeepSeek 侧完成，按成功请求计费（5 元 / 1000 次，约 ¥0.005/次）。

---

## 目录

- [工作原理](#工作原理)
- [安装](#安装)
- [客户端配置](#客户端配置)
- [验证](#验证)
- [工具参考](#工具参考)
- [计费](#计费)
- [排障](#排障)
- [复用与改造](#复用与改造)
- [目录结构](#目录结构)
- [安全](#安全)

---

## 工作原理

### 1. 进程模型：stdio，按需拉起

```
┌──────────────┐   JSON-RPC over stdin/stdout   ┌────────────┐   HTTPS POST   ┌─────────────────────┐
│ MCP 客户端   │ ◄────────────────────────────► │ server.py  │ ─────────────► │ api.deepseek.com    │
│ (Claude /    │    tools/list · tools/call     │ (FastMCP)  │  /v1/tools/*   │ /v1/tools/search    │
│  Cursor /    │                                │            │ ◄───────────── │ /v1/tools/fetch     │
│  QwenPaw)    │                                └────────────┘   JSON 结果    └─────────────────────┘
└──────────────┘
```

- 服务器**不是常驻服务**：客户端需要时启动 `python server.py`，通过 stdin/stdout 交换 JSON-RPC，进程退出即结束。
- 因此**不占端口、不需要守护进程、不需要反向代理**。
- 代价是每条日志都必须写 stderr——stdout 被 jsonrpc 独占，多加一个 `print()` 就会破坏协议（见 [排障](#排障)）。

### 2. 一次调用的完整链路

以 `deepseek_search` 为例，代码路径全部在 `server.py`：

1. **注册**：`@mcp.tool()` 装饰 `deepseek_search()`，FastMCP 从函数签名 + docstring 自动生成 `tools/list` 的 JSON Schema（参数名、类型、默认值、描述都由 Python 函数定义推导）。
2. **入参整形**：`lang` 拼成 `lang:<值>` 追加到 query；`count` 收敛到 1–50；`ori_question` 截断 128 字符。
3. **发请求**：`_post("/v1/tools/search", payload)` —— 只做一件事：带 `Authorization: Bearer <key>` POST JSON，超时 `DEEPSEEK_TIMEOUT`。
4. **错误归一**：HTTP 4xx/5xx 原样保留上游 body，返回 `{"error": "HTTP 401", "detail": {...}}`；网络异常返回 `{"error": "URLError: ..."}`。模型能直接读到失败原因，不会拿到空字符串瞎猜。
5. **瘦身**：上游每条结果有十几个字段，这里只保留 8 个对模型有用的（`title/url/summary/snippet/site_name/date/authority/rerank_score`），避免把整包 JSON 塞进上下文浪费 token。
6. **序列化**：`json.dumps(..., ensure_ascii=False)` —— 中文原样输出，不转成 `\uXXXX`。

`deepseek_fetch` 同链路，只是 payload 只有 `{"url": ...}`，返回 `{"title", "content"}`。

### 3. API Key 从哪里来（四级解析）

`server.py` 的 `resolve_api_key()` 按顺序尝试，**先命中先返回**：

| 顺序 | 来源 | 适用对象 | 代码位置 |
|---|---|---|---|
| 1 | 环境变量 `DEEPSEEK_API_KEY` | 所有客户端（最通用） | `resolve_api_key()` |
| 2 | `DEEPSEEK_API_KEY_FILE` 指向的文件（取首个非空行） | 不想把 key 写进 GUI 配置的人 | `_from_file()` |
| 3 | QwenPaw 内置 DeepSeek 提供商的 secret | **已配过 DeepSeek 提供商的 QwenPaw 用户** | `_from_qwenpaw_provider()` |
| 4 | 都没有 → 返回结构化错误 | —— | `_post()` |

### 4. 为什么「配过 DeepSeek 提供商就不用再填 key」

QwenPaw 用户通常已经在应用内配好 DeepSeek（模型能对话就说明 key 有效）。这个项目把那份 key 直接复用，省掉重复填写：

```
~/.qwenpaw.secret/providers/builtin/deepseek.json
        └── { "api_key": "ENC:xxxx..." }      ← QwenPaw 加密存储，不是明文
```

关键点是 `ENC:` 前缀——这是 QwenPaw 用自己的 master key 加密后的密文，**直接拿去请求会 401**。所以 `_decrypt_if_needed()` 做了三件事：

1. 没有 `ENC:` 前缀 → 当明文，原样返回；
2. 有 `ENC:` 且本机装了 QwenPaw → `from qwenpaw.security.secret_store import decrypt` 解密；
3. 有 `ENC:` 但没装 QwenPaw / 解密失败 → 返回空串，**静默跳到下一级**，不影响其他客户端。

这就是「只有已配置 DeepSeek 提供商的 QwenPaw 环境才免填 key」的实现方式：免填不是猜出来的，是读同一份 secret 并解密。非 QwenPaw 环境（Claude Desktop、Cursor）不会走到这一级，仍按第 1、2 级取 key。

> 自检：`python server.py --check-key` 会打印命中的来源（`qwenpaw:provider` / `env:...` / `file:...`）和 key 长度，**不打印 key 本身**。

---

## 安装

需要 Python 3.10+。

```bash
git clone https://github.com/BillYuan01/deepseek-search-mcp.git
cd deepseek-search-mcp

python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python server.py --check-key    # 先确认 key 能取到
```

或用 [uv](https://docs.astral.sh/uv/)：

```bash
uv run --with mcp python server.py
```

## 客户端配置

### 通用 `mcpServers` 格式

```json
{
  "mcpServers": {
    "deepseek": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server.py"],
      "env": { "DEEPSEEK_API_KEY": "sk-xxxxxxxx" }
    }
  }
}
```

两个要点：

- `command` / `args` 必须是**绝对路径**。GUI 启动的客户端不继承你 shell 里的 PATH 和 alias。
- key 放在 `env` 里，不要写进会被提交的文件。这个 JSON 只存在本机。

### Claude Desktop

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

把上面的 JSON 合并进去，完全退出再重启。完整示例见 [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)。

### Cursor / Cline / Continue

同样用 `mcpServers` 结构，粘进各自的 MCP 设置即可。

### QwenPaw（driver 形式）

```yaml
name: deepseek_search
protocol: mcp
endpoint:
  transport: stdio
  command: /absolute/path/to/.venv/bin/python
  args: ["/absolute/path/to/server.py"]
  cwd: /absolute/path/to/deepseek-search-mcp
  env: {}
enabled: true
config:
  display_name: deepseek
  description: DeepSeek 官方 Search / Fetch API
```

`env` 留空即可——如果这台机器的 QwenPaw 已经配好 DeepSeek 提供商，第 3 级会自动命中。示例见 [`examples/qwenpaw-driver.yaml`](examples/qwenpaw-driver.yaml)。

## 验证

```bash
python server.py --check-key   # 只验 key 来源，不花钱
python test_client.py          # 完整链路：list_tools + search + fetch（约 ¥0.01）
```

预期输出：

```
key 来源: qwenpaw:provider（长度 35）
tools: ['deepseek_fetch', 'deepseek_search']
search ok: 3 results
fetch ok: Example Domain
```

`test_client.py` 用的是官方 `mcp` 客户端 + `stdio_client`，和真实客户端的连接方式完全一致，所以它过了基本等于客户端能用。

## 工具参考

### `deepseek_search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索词，1–128 字符；支持 `site:` `-site:` `-term` `"精确短语"` `lang:` `after:` `before:` |
| `count` | int | 5 | 返回条数，自动收敛到 1–50 |
| `need_content` | bool | false | true 时额外返回整页正文 `content` |
| `ori_question` | string | "" | 触发搜索的用户原始问题，有助于提升召回（截断 128） |
| `lang` | string | "" | 便捷参数，拼成 `lang:<值>` 追加到 query，如 `zh`、`en` |

返回：

```json
{
  "results": [
    {
      "title": "…",
      "url": "https://…",
      "summary": "…",
      "snippet": "…",
      "site_name": "…",
      "date": "2026-01-01",
      "authority": "…",
      "rerank_score": 0.87
    }
  ]
}
```

### `deepseek_fetch`

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | 必须是 http/https 完整 URL，不含用户名密码，长度 ≤ 8192 |

返回：

```json
{ "title": "页面标题", "content": "# Markdown 正文……" }
```

两个工具的失败返回统一为 `{"error": "...", "detail": ...}`。

## 计费

- 官方口径：**5 元 / 1000 次成功请求**，search 与 fetch 同价，单次约 ¥0.005。
- 计费以 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search) 为准；本项目是薄封装，不加价、不转发第三方。
- `count` 与 `need_content` 不影响单价：一次请求就是一次。

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| `HTTP 401` | key 无效 / 首尾带空格 / 已吊销 | 重新生成；`--check-key` 看命中的是哪一级 |
| `HTTP 402` | 余额不足 | 到 DeepSeek 控制台充值 |
| `HTTP 403` | key 无工具 API 权限 | 确认账号已开通 |
| `HTTP 429` | 触发限流 | 降并发，退避重试 |
| `缺少 DEEPSEEK_API_KEY` | 客户端没把 env 传进来，且本机无 QwenPaw provider | 在配置的 `env` 里补 key |
| 客户端一直「连接中」 | `command` 路径错 / 依赖没装 | 手动 `python server.py` 看 stderr |
| 客户端报 JSON 解析错误 | stdout 被日志污染 | 所有调试输出改用 `print(..., file=sys.stderr)` |
| 慢页面超时 | 默认 60 秒不够 | 调大 `DEEPSEEK_TIMEOUT` |

## 复用与改造

这个仓库的结构可以当模板，换成任何「HTTP API → MCP 工具」的场景：

1. **换后端**：改 `BASE_URL`（也支持 `DEEPSEEK_BASE_URL` 环境变量）与 `_post()` 里的 path，例如把 `/v1/tools/search` 换成你自己的接口。
2. **加工具**：新写一个 `@mcp.tool()` 函数，签名 + docstring 就是给模型看的 schema，不需要手写 JSON Schema。
3. **改 key 来源**：在 `resolve_api_key()` 里插一级，保持「先专用后通用」的顺序即可。
4. **多实例**：同一个 `server.py` 可以被配置多次，用不同 `env`（不同 key / 不同 base_url）区分，客户端里会显示成两个独立服务器。

## 目录结构

```
.
├── server.py                        # 全部逻辑：key 解析 + 两个工具 + 错误归一
├── test_client.py                   # 冒烟测试（官方 mcp 客户端直连 stdio）
├── requirements.txt                 # mcp>=1.0.0
├── examples/
│   ├── claude_desktop_config.json   # Claude Desktop 配置模板
│   └── qwenpaw-driver.yaml          # QwenPaw driver 配置模板
├── AGENTS.md                        # 给 AI 编码助手的改动约定
├── LICENSE                          # MIT
└── README.md
```

## 安全

- key 只通过环境变量 / 文件 / QwenPaw secret 注入，代码里没有硬编码凭据。
- `.gitignore` 覆盖 `.env`、`.venv/`、`__pycache__/`。
- 服务器不读写你的本地文件（除读取 key 的那一次），只向 `api.deepseek.com` 发请求。

## License

[MIT](LICENSE)

---

## English

**deepseek-search-mcp** exposes DeepSeek's official Search and Fetch APIs as an MCP server over stdio.

- `deepseek_search(query, count=5, need_content=False, ori_question="", lang="")` — web search returning titles, URLs, snippets, site names, dates and authority scores.
- `deepseek_fetch(url)` — fetch one page and return its main content as Markdown.

The API key is resolved in four steps: `DEEPSEEK_API_KEY` → `DEEPSEEK_API_KEY_FILE` → the QwenPaw built-in DeepSeek provider secret (decrypted transparently, so QwenPaw users who already configured DeepSeek don't need to enter a key again) → a structured error. Non-QwenPaw clients simply skip step 3.

```bash
pip install -r requirements.txt
python server.py --check-key   # show which source was hit, without printing the key
python test_client.py          # end-to-end smoke test (~CNY 0.01)
```

Pricing follows DeepSeek's official rate (CNY 5 per 1,000 successful requests). MIT licensed.
