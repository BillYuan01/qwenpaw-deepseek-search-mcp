# deepseek-search-mcp

把 **DeepSeek 官方 Search / Fetch API** 包成一个 MCP 服务器（stdio），给 Claude Desktop、Cursor、Cline、QwenPaw 等任意 MCP 客户端用：

- `deepseek_search` —— 联网检索，返回标题 / URL / 摘要 / 站点 / 时间 / 权威度
- `deepseek_fetch` —— 抓单个网页，转成干净的 Markdown 正文

不用自己维护索引和爬虫：检索、清洗、正文提取都在 DeepSeek 侧完成，按成功请求计费（5 元 / 1000 次，约 ¥0.005/次）。

---

## 特性

- **纯 stdio**：一次请求拉起进程，跑完即退，不常驻、不占端口
- **依赖极少**：Python 标准库 + `mcp`，不需要 `requests`、不需要浏览器
- **检索有操作符**：`site:` / `-site:` / `-term` / `"精确短语"` / `lang:` / `after:` / `before:`
- **正文即 Markdown**：`deepseek_fetch` 直接返回可读正文，适合喂给模型或写进笔记
- **错误原样透传**：401 / 402 / 403 / 429 保留上游原始报错，方便定位是 key、余额还是限流

## 工具

### `deepseek_search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索词，1–128 字符 |
| `count` | int | 5 | 返回条数，自动收敛到 1–50 |
| `need_content` | bool | false | true 时额外返回整页正文 `content` |
| `ori_question` | string | "" | 触发搜索的用户原始问题，有助于提升召回（截断 128） |
| `lang` | string | "" | 便捷参数，会拼成 `lang:<值>` 追加到 query，如 `zh`、`en` |

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

出错时统一返回 `{"error": "…", "detail": …}`，模型可以直接读到原因。

## 安装

需要 Python 3.10+。

```bash
git clone https://github.com/BillYuan01/deepseek-search-mcp.git
cd deepseek-search-mcp

python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DEEPSEEK_API_KEY=sk-xxxxxxxx   # Windows PowerShell: $env:DEEPSEEK_API_KEY="sk-…"

python server.py               # 直接运行是 stdio 模式，屏幕没有输出是正常的
```

不想用虚拟环境，用 [uv](https://docs.astral.sh/uv/) 更省事：

```bash
uv run --with mcp python server.py
```

## 客户端配置

### 通用 `mcpServers` 格式

```json
{
  "mcpServers": {
    "deepseek": {
      "command": "/absolute/path/to/deepseek-search-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/deepseek-search-mcp/server.py"],
      "env": { "DEEPSEEK_API_KEY": "sk-xxxxxxxx" }
    }
  }
}
```

> `command` / `args` 要写**绝对路径**。macOS 上图形界面启动的 App 读不到你 shell 里的环境变量，所以 key 建议直接放在 `env` 里（该文件只存在本机，不要提交到仓库）。

### Claude Desktop

配置文件位置：

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

把上面的 JSON 合并进去，重启客户端。完整示例见 [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)。

### Cursor / Cline / Continue

同样用 `mcpServers` 结构，粘贴到各自的 MCP 设置里即可。

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

示例文件见 [`examples/qwenpaw-driver.yaml`](examples/qwenpaw-driver.yaml)。

## 计费

- 官方口径：**5 元 / 1000 次成功请求**，search 与 fetch 同价，单次约 ¥0.005。
- 计费规则以 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search) 为准；本项目只是一层薄封装，不额外加价、不转发到第三方。
- `count` 和 `need_content` **不影响单价**，一次请求就是一次。

## 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `HTTP 401` | key 无效 / 复制时带了空格 / 已吊销 | 重新生成 key，注意首尾空白 |
| `HTTP 402` | 账户余额不足 | 到 DeepSeek 控制台充值 |
| `HTTP 403` | key 无权访问该接口 | 确认账号已开通工具 API |
| `HTTP 429` | 触发限流 | 降低并发，退避重试 |
| `缺少 DEEPSEEK_API_KEY` | 客户端没把 env 传进来 | 检查配置里的 `env` 字段 |
| 客户端一直“连接中” | `command` 路径错 / 依赖没装 | 手动跑一遍 `python server.py`，看 stderr |

排障时把 `DEEPSEEK_TIMEOUT` 调大（默认 60 秒）可以排除慢页面超时。

## 安全

- key 只通过 `env` 或 `DEEPSEEK_API_KEY_FILE` 注入，代码里没有任何硬编码凭据。
- `.gitignore` 已覆盖 `.env`、`.venv/`、`__pycache__/`。
- 服务器不读写你的本地文件，只往 `api.deepseek.com` 发请求。

## 自测

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxx
python test_client.py            # 列出工具；有 key 时顺带跑一次 search + fetch
```

预期输出：

```
tools: ['deepseek_search', 'deepseek_fetch']
search ok: 3 results
fetch ok: Example Domain
```

## License

[MIT](LICENSE)

---

## English

**deepseek-search-mcp** exposes DeepSeek's official Search and Fetch APIs as an MCP server over stdio.

Two tools:

- `deepseek_search(query, count=5, need_content=False, ori_question="", lang="")` — web search with titles, URLs, snippets, site names, dates and authority scores.
- `deepseek_fetch(url)` — fetch a single page and return its main content as Markdown.

Requirements: Python 3.10+, `pip install -r requirements.txt`, and `DEEPSEEK_API_KEY` in the environment. Works with any MCP client (Claude Desktop, Cursor, Cline, QwenPaw…). Pricing follows DeepSeek's official rate (CNY 5 per 1,000 successful requests); this project is a thin wrapper and adds no markup.

```bash
git clone https://github.com/BillYuan01/deepseek-search-mcp.git
cd deepseek-search-mcp
pip install -r requirements.txt
DEEPSEEK_API_KEY=sk-xxx python server.py
```

MIT licensed.
