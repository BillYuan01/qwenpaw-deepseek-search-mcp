# qwenpaw-deepseek-search-mcp

QwenPaw 的 DeepSeek 搜索与抓取 MCP 服务器。安装后，QwenPaw 里的 agent 会多出两个工具：

| 工具 | 作用 | 使用场景 |
|---|---|---|
| `deepseek_search` | 联网检索，返回标题、URL、摘要、站点、时间、权威度 | 查询训练数据之后发生的事 |
| `deepseek_fetch` | 抓取单个网页，转成 Markdown 正文 | 读取搜索结果全文，用于总结、对比或入库 |

如果 QwenPaw 里已经配置好 DeepSeek 提供商（能正常对话即可），安装后不需要再填 API key。服务器会读取 QwenPaw 保存的 provider key 并解密使用。实现方式见 [免填 key 的处理](#免填-key-的处理)。

## 目录

- [工作原理](#工作原理)
- [安装到 QwenPaw](#安装到-qwenpaw)
- [验证](#验证)
- [key 解析](#key-解析)
- [工具参考](#工具参考)
- [计费](#计费)
- [排障](#排障)
- [复用与改造](#复用与改造)
- [目录结构](#目录结构)
- [安全](#安全)

## 工作原理

### QwenPaw 如何加载

```
┌──────────────────────────┐
│ QwenPaw                  │
│  agent（default / 其他） │
│    │                     │
│    ├─ drivers/mcp/*.yaml │  配置启动命令、参数、env、暴露策略
│    │        │            │
│    │        ▼            │
│    │   QwenPaw 管理 → MCP 卡片（显示为 deepseek）
│    │        │            │
│    │        ▼  按需启动子进程
│    │   ┌──────────┐  stdin/stdout  ┌──────────────────┐
│    └──►│ server.py│◄──────────────►│ Agent 的工具列表 │
│        │ FastMCP  │   JSON-RPC     └──────────────────┘
│        └────┬─────┘
└─────────────┼──────────────┘
              │ HTTPS POST
              ▼
      api.deepseek.com/v1/tools/search | /v1/tools/fetch
```

- 服务器按需启动。QwenPaw 在需要时运行 `python server.py`，通过 stdio 收发 JSON-RPC，调用结束后进程退出，不占端口，也不需要守护进程或反向代理。
- 管理页里一张卡片对应一个服务器进程。`deepseek` 卡片包含 `deepseek_search` 和 `deepseek_fetch` 两个工具，可以在卡片的「配置」中查看。
- 工具名会带前缀，agent 侧看到的是 `deepseek__deepseek_search`。前缀来自 MCP 客户端名，同时安装多个同类服务器时不会冲突。

### 一次工具调用的过程

以 `deepseek_search` 为例，相关代码都在 `server.py`：

1. 注册：`@mcp.tool()` 装饰 `deepseek_search()`，FastMCP 根据函数签名和 docstring 生成 `tools/list` 的 JSON Schema。参数名、类型、默认值和描述都来自函数定义，不需要手写 schema。
2. 参数整理：`lang` 拼成 `lang:<值>` 追加到 query；`count` 限制在 1 到 50；`ori_question` 截断到 128 字符。
3. 发起请求：`_post("/v1/tools/search", payload)` 带上 `Authorization: Bearer <key>`，以 JSON 形式 POST。超时由 `DEEPSEEK_TIMEOUT` 控制，默认 60 秒。
4. 错误处理：HTTP 4xx/5xx 保留上游返回的 body，输出 `{"error": "HTTP 401", "detail": {...}}`；网络异常输出 `{"error": "URLError: ..."}`。agent 可以直接读到失败原因。
5. 裁剪字段：上游每条结果有十几个字段，这里只保留 8 个（`title`、`url`、`summary`、`snippet`、`site_name`、`date`、`authority`、`rerank_score`），减少上下文占用。
6. 序列化：`json.dumps(..., ensure_ascii=False)`，中文按原样输出。

`deepseek_fetch` 使用同样的流程，请求体只有 `{"url": ...}`，返回 `{"title", "content"}`。

### key 解析

`resolve_api_key()` 按以下顺序查找，命中即返回：

| 顺序 | 来源 | 适用情况 |
|---|---|---|
| 1 | 环境变量 `DEEPSEEK_API_KEY` | 手动指定 key |
| 2 | `DEEPSEEK_API_KEY_FILE` 指向的文件（取首个非空行） | 不希望把 key 写进 yaml |
| 3 | QwenPaw 内置 DeepSeek 提供商的 secret | 已在 QwenPaw 配置过 DeepSeek 提供商 |
| 4 | 以上都没有，返回错误并列出尝试过的来源 | 无 |

### 免填 key 的处理

QwenPaw 将 provider 的 key 加密保存在：

```
~/.qwenpaw.secret/providers/builtin/deepseek.json
        └── { "api_key": "ENC:xxxx..." }
```

带 `ENC:` 前缀的内容是密文，直接用于请求会返回 401。`_decrypt_if_needed()` 按以下逻辑处理：

1. 没有 `ENC:` 前缀，按明文返回。
2. 有前缀且本机安装了 QwenPaw，调用 `qwenpaw.security.secret_store.decrypt` 解密。
3. 有前缀但解密失败（缺少 master key 或版本不匹配），返回空字符串，继续尝试下一个来源。

`resolve_api_key()` 只有在前两级都为空时才会读取这份 secret。没有配置 DeepSeek 提供商的环境不会命中第 3 级，最终会进入第 4 级并给出明确的错误信息。

## 安装到 QwenPaw

### 准备

```bash
git clone https://github.com/BillYuan01/qwenpaw-deepseek-search-mcp.git
cd qwenpaw-deepseek-search-mcp

python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

需要 Python 3.10 以上版本。

### 配置 driver

每个 agent 有独立的 MCP 配置目录：

```
~/.qwenpaw/workspaces/<agent-id>/drivers/mcp/<名字>.yaml
```

例如 `default` agent 对应 `~/.qwenpaw/workspaces/default/drivers/mcp/deepseek_search.yaml`。新建该文件：

```yaml
name: deepseek_search
protocol: mcp
endpoint:
  transport: stdio
  command: /absolute/path/to/qwenpaw-deepseek-search-mcp/.venv/bin/python
  args:
    - /absolute/path/to/qwenpaw-deepseek-search-mcp/server.py
  cwd: /absolute/path/to/qwenpaw-deepseek-search-mcp
  env: {}
credentials: {}
config:
  display_name: deepseek
  description: DeepSeek 官方 Search / Fetch API（网页检索 + 正文抓取）
enabled: true
policy:
  default_effect: deny
  rules:
    - subject: '*'
      effect: ask
      target:
        kind: tool
        name: '*'
      principal:
        source_type: '*'
        source_value: '*'
        subject_type: '*'
        subject_value: '*'
      condition: null
```

- `env` 留空。如果 QwenPaw 已配置 DeepSeek 提供商，第 3 级会自动命中。
- `command` 和 `args` 使用绝对路径。QwenPaw 启动子进程时的工作目录不确定。
- `policy` 控制工具暴露和审批。上面的配置是默认拒绝、调用时询问，可以按需要调整。

### 界面确认

打开 QwenPaw 管理 → MCP，应看到名为 `deepseek` 的卡片：

- 标注「本地 stdio」
- 开关处于打开状态
- 点「配置」可以看到 `deepseek_search` 和 `deepseek_fetch` 两项，均为勾选状态

该页面不会自动轮询。从外部修改 yaml 后，需要刷新页面才能看到新卡片。

### 重启会话

MCP 工具在会话启动时注入。已经在运行的会话看不到新工具，新建会话即可，不需要重启 QwenPaw 服务。

## 验证

按成本从低到高：

```bash
# 只检查 key 来源，不产生调用费用
python server.py --check-key
# OK   来源: qwenpaw:provider   长度: 35

# 完整链路：list_tools + search + fetch，约 ¥0.01
python test_client.py
# key 来源: qwenpaw:provider（长度 35）
# tools: ['deepseek_fetch', 'deepseek_search']
# search ok: 3 results
# fetch ok: Example Domain

# 在 QwenPaw 新建会话中测试
# 「用 deepseek_search 查一下今天的 AI 新闻」
```

`--check-key` 只打印来源和长度，不打印 key 本身。`test_client.py` 使用官方 `mcp` 客户端加 `stdio_client`，连接方式与 QwenPaw 一致。

## 工具参考

### `deepseek_search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索词，1 到 128 字符；支持 `site:` `-site:` `-term` `"精确短语"` `lang:` `after:` `before:` |
| `count` | int | 5 | 返回条数，限制在 1 到 50 |
| `need_content` | bool | false | 为 true 时额外返回整页正文 `content` |
| `ori_question` | string | "" | 用户原始问题，用于改善检索，截断到 128 字符 |
| `lang` | string | "" | 拼成 `lang:<值>` 追加到 query，如 `zh`、`en` |

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
| `url` | string | 必须是 http/https 完整 URL，不含用户名密码，长度不超过 8192 |

返回：

```json
{ "title": "页面标题", "content": "# Markdown 正文……" }
```

两个工具出错时都返回 `{"error": "...", "detail": ...}`。

## 计费

- 官方定价：5 元 / 1000 次成功请求，search 与 fetch 同价，单次约 ¥0.005。
- 计费规则以 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search) 为准。本项目只做封装，不加价，也不转发到第三方。
- `count` 和 `need_content` 不影响单价，一次请求计一次。

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 管理页看不到卡片 | 修改 yaml 后没有刷新页面 | 刷新页面；确认文件位于当前 agent 的 `drivers/mcp/` 下 |
| 卡片存在，agent 无法调用 | 旧会话没有注入新工具 | 新建会话 |
| 日志出现 `MCP server not configured` | 该 agent 的 MCP 配置未加载或路径错误 | 检查 `drivers/mcp/*.yaml` 的 `enabled` 和路径 |
| 调用时要求批准 | `policy` 设置为 `ask` | 在卡片或 policy 中调整为允许 |
| `HTTP 401` | key 无效，或 `ENC:` 密文未解密 | 用 `--check-key` 查看来源；来源为 `qwenpaw:provider` 仍报 401 说明解密失败 |
| `HTTP 402` | 账户余额不足 | 到 DeepSeek 控制台充值 |
| `HTTP 403` | key 没有工具 API 权限 | 确认账号已开通 |
| `HTTP 429` | 触发限流 | 降低并发，退避重试 |
| `缺少 DEEPSEEK_API_KEY` | 三级来源均未命中 | 配置 QwenPaw 的 DeepSeek 提供商，或在 `env` 中填写 key |
| 客户端一直显示连接中 | `command` 路径错误或依赖未安装 | 手动运行 `python server.py` 查看 stderr |
| 客户端报 JSON 解析错误 | stdout 被日志污染 | 调试输出改用 `print(..., file=sys.stderr)` |

## 复用与改造

这套结构可以复用到其他 HTTP API：

1. 更换后端：修改 `BASE_URL`（也支持环境变量 `DEEPSEEK_BASE_URL`）和 `_post()` 中的 path。
2. 增加工具：新增一个 `@mcp.tool()` 函数，签名和 docstring 会直接生成 schema。
3. 修改 key 来源：在 `resolve_api_key()` 中插入一级，保持先具体后通用的顺序。
4. 多实例：同一个 `server.py` 可以配置多次，用不同的 `env`（不同 key 或 base_url）区分，界面中显示为两张独立卡片。

## 目录结构

```
.
├── server.py                   # key 解析、两个工具、错误处理
├── test_client.py              # 冒烟测试，使用官方 mcp 客户端直连 stdio
├── requirements.txt            # mcp>=1.0.0
├── examples/
│   └── qwenpaw-driver.yaml     # driver 配置模板
├── AGENTS.md                   # 代码修改约定
├── LICENSE                     # MIT
└── README.md
```

## 安全

- key 通过环境变量、文件或 QwenPaw secret 注入，代码中没有硬编码凭据。
- `.gitignore` 包含 `.env`、`.venv/`、`__pycache__/`。
- 服务器除读取 key 外不访问本地文件，网络请求只发往 `api.deepseek.com`。

## License

[MIT](LICENSE)

---

标准 stdio MCP 服务器，任何 MCP 客户端都可以运行 `python server.py`。
