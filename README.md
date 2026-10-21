# qwenpaw-deepseek-search-mcp

给 **QwenPaw** 用的 DeepSeek 搜索 / 抓取 MCP 服务器。

装上之后，QwenPaw 里的 agent 多两个工具：

| 工具 | 作用 | agent 什么时候会用 |
|---|---|---|
| `deepseek_search` | 联网检索，返回标题 / URL / 摘要 / 站点 / 时间 / 权威度 | 问「最新」「今天」「查一下」这类超出训练数据的问题 |
| `deepseek_fetch` | 抓单个网页，转成干净的 Markdown 正文 | 拿到搜索结果后读全文，再总结、对比或入库 |

**这个项目的核心不是「又一个搜索 MCP」，而是免填 key**：只要你的 QwenPaw 已经配好 DeepSeek 提供商（能正常对话就说明配好了），MCP 会自己读那份 key 并解密，你不用在任何地方再填一遍。

---

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

---

## 工作原理

### 1. QwenPaw 如何加载这个 MCP

```
┌──────────────────────────┐
│ QwenPaw                  │
│  agent（default / 其他） │
│    │                     │
│    ├─ drivers/mcp/*.yaml │  声明式配置：启动命令、参数、env、暴露策略
│    │        │            │
│    │        ▼            │
│    │   QwenPaw 管理 → MCP 卡片（显示为 deepseek）
│    │        │            │
│    │        ▼  按需拉起子进程
│    │   ┌──────────┐  stdin/stdout  ┌──────────────────┐
│    └──►│ server.py│◄──────────────►│ Agent 的工具列表 │
│        │ FastMCP  │   JSON-RPC     └──────────────────┘
│        └────┬─────┘
└─────────────┼──────────────┘
              │ HTTPS POST
              ▼
      api.deepseek.com/v1/tools/search | /v1/tools/fetch
```

要点：

- **不是常驻服务**。QwenPaw 在需要时拉起 `python server.py`，通过 stdio 交换 JSON-RPC，用完退出——不占端口、不用配守护进程、不用反向代理。
- **一张卡 = 一个服务器进程**，不是「一个工具一张卡」。`deepseek` 卡片里装的是 `deepseek_search` 和 `deepseek_fetch` 两个工具，在卡片的「配置」里能看到。
- **工具会带命名空间前缀**。agent 实际看到的是 `deepseek__deepseek_search` 这种形式，前缀来自 MCP 客户端名，所以你可以同时装多个同类服务器而不冲突。

### 2. 一次工具调用发生了什么

以 `deepseek_search` 为例，代码全在 `server.py`：

1. **注册**：`@mcp.tool()` 装饰 `deepseek_search()`，FastMCP 从函数签名 + docstring 自动生成 `tools/list` 的 JSON Schema——参数名、类型、默认值、描述都由 Python 函数定义推导，没有手写 schema。
2. **入参整形**：`lang` 拼成 `lang:<值>` 追加到 query；`count` 收敛到 1–50；`ori_question` 截断 128 字符。
3. **发请求**：`_post("/v1/tools/search", payload)`，带 `Authorization: Bearer <key>` POST JSON，超时由 `DEEPSEEK_TIMEOUT` 控制（默认 60 秒）。
4. **错误归一**：HTTP 4xx/5xx 保留上游原始 body，返回 `{"error": "HTTP 401", "detail": {...}}`；网络异常返回 `{"error": "URLError: ..."}`。agent 能直接读到失败原因，不会拿到空字符串瞎猜。
5. **瘦身**：上游每条结果有十几个字段，只保留 8 个对模型有用的（`title/url/summary/snippet/site_name/date/authority/rerank_score`），避免把整包 JSON 塞进上下文浪费 token。
6. **序列化**：`json.dumps(..., ensure_ascii=False)`，中文原样输出。

`deepseek_fetch` 同一条链路，payload 只有 `{"url": ...}`，返回 `{"title", "content"}`。

### 3. key 解析（第 3 级专为 QwenPaw）

`server.py` 的 `resolve_api_key()` 按顺序尝试，先命中先返回：

| 顺序 | 来源 | 谁会用 |
|---|---|---|
| 1 | 环境变量 `DEEPSEEK_API_KEY` | 手动指定 key 的情况 |
| 2 | `DEEPSEEK_API_KEY_FILE` 指向的文件（首个非空行） | 不想把 key 写进 yaml 的情况 |
| 3 | **QwenPaw 内置 DeepSeek 提供商的 secret** | **已配过 DeepSeek 提供商的 QwenPaw 用户** |
| 4 | 都没有 → 结构化错误，列出试过的三级 | —— |

### 4. 为什么「配过 DeepSeek 提供商就不用填 key」

QwenPaw 把 provider 的 key 加密存在这里：

```
~/.qwenpaw.secret/providers/builtin/deepseek.json
        └── { "api_key": "ENC:xxxx..." }     ← 密文，不是明文
```

`ENC:` 是 QwenPaw 用自己的 master key 加密后的格式，**直接拿去请求会 401**。所以 `_decrypt_if_needed()` 做了三件事：

1. 不带 `ENC:` 前缀 → 当明文，原样返回；
2. 带 `ENC:` 且本机装了 QwenPaw → `from qwenpaw.security.secret_store import decrypt` 解密；
3. 带 `ENC:` 但解密失败（master key 缺失、版本不匹配）→ 返回空串，**静默跳到下一级**，不抛异常。

所以「免填」不是猜的，是读同一份 secret 并解密。判断条件也很明确：**只有已经配好 DeepSeek 提供商的 QwenPaw 环境才会命中第 3 级**；没配过的环境会落到第 4 级，报错里会写清楚试过哪些来源。

---

## 安装到 QwenPaw

### 0. 准备

```bash
git clone https://github.com/BillYuan01/qwenpaw-deepseek-search-mcp.git
cd qwenpaw-deepseek-search-mcp

python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

需要 Python 3.10+。

### 1. 放进 QwenPaw

每个 agent 有独立的 MCP 配置目录：

```
~/.qwenpaw/workspaces/<agent-id>/drivers/mcp/<名字>.yaml
```

例如 `default` agent 就是 `~/.qwenpaw/workspaces/default/drivers/mcp/deepseek_search.yaml`。新建这个文件：

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

三点说明：

- `env` 留空即可——如果你的 QwenPaw 已经配好 DeepSeek 提供商，第 3 级会自动命中。
- `command` / `args` 必须写**绝对路径**。QwenPaw 拉起子进程时的工作目录不一定是你的 shell 目录。
- `policy` 控制暴露与审批。上面这份是「默认拒绝，工具调用走 ask」，可以在卡片里按需放宽。

### 2. 在界面里确认

打开 **QwenPaw 管理 → MCP**，应该看到一张名为 `deepseek` 的卡片：

- 标注「本地 stdio」
- 右上角开关是打开状态
- 点「配置」能看到 `deepseek_search` 和 `deepseek_fetch` 两项，都勾选

> 注意：这个页面**不会自动轮询**。你是从外部改的 yaml，需要刷新页面才看得到新卡片。

### 3. 重启会话

MCP 工具在会话启动时注入。已经在跑的会话看不到新工具，**开一个新会话**即可，不用重启 QwenPaw 服务。

---

## 验证

三层验证，从便宜到贵：

```bash
# 1. 只验 key 来源，不花钱
python server.py --check-key
# OK   来源: qwenpaw:provider   长度: 35

# 2. 完整链路：list_tools + search + fetch（约 ¥0.01）
python test_client.py
# key 来源: qwenpaw:provider（长度 35）
# tools: ['deepseek_fetch', 'deepseek_search']
# search ok: 3 results
# fetch ok: Example Domain

# 3. 在 QwenPaw 新会话里问一句
#    「用 deepseek_search 查一下今天的 AI 新闻」
```

`--check-key` 只打印来源和长度，**永远不打印 key 本身**，可以放心贴给别人看。`test_client.py` 用的是官方 `mcp` 客户端 + `stdio_client`，和 QwenPaw 的连接方式一致，所以它过了基本等于 QwenPaw 能用。

---

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

---

## 计费

- 官方口径：**5 元 / 1000 次成功请求**，search 与 fetch 同价，单次约 ¥0.005。
- 计费以 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search) 为准；本项目是薄封装，不加价、不转发第三方。
- `count` 与 `need_content` 不影响单价：一次请求就是一次。

---

## 排障

| 现象 | 原因 | 处理 |
|---|---|---|
| QwenPaw 管理页看不到卡片 | 改完 yaml 没刷新页面 | 刷新；确认文件在**当前 agent** 的 `drivers/mcp/` 下 |
| 卡片在，agent 却用不了工具 | 旧会话没注入新工具 | 开新会话 |
| 日志出现 `MCP server not configured` | 该 agent 的 MCP 配置未加载/路径不对 | 检查 `drivers/mcp/*.yaml` 的 `enabled` 与路径 |
| 调用时弹批准 | `policy` 是 `ask` | 在卡片或 policy 里调整到允许 |
| `HTTP 401` | key 无效，或 `ENC:` 密文没解开 | `--check-key` 看命中来源；`qwenpaw:provider` 却 401 说明解密失败 |
| `HTTP 402` | 余额不足 | 到 DeepSeek 控制台充值 |
| `HTTP 403` | key 无工具 API 权限 | 确认账号已开通 |
| `HTTP 429` | 触发限流 | 降并发，退避重试 |
| `缺少 DEEPSEEK_API_KEY` | 三级全没命中 | 配好 QwenPaw 的 DeepSeek 提供商，或在 `env` 里填 key |
| 客户端一直「连接中」 | `command` 路径错 / 依赖没装 | 手动 `python server.py` 看 stderr |
| 客户端报 JSON 解析错误 | stdout 被日志污染 | 调试输出改用 `print(..., file=sys.stderr)` |

---

## 复用与改造

这个仓库可以当「HTTP API → QwenPaw MCP」的模板：

1. **换后端**：改 `BASE_URL`（也支持 `DEEPSEEK_BASE_URL`）与 `_post()` 里的 path。
2. **加工具**：新写一个 `@mcp.tool()` 函数，签名 + docstring 就是给模型看的 schema。
3. **改 key 来源**：在 `resolve_api_key()` 里插一级，保持「先专用后通用」。
4. **多实例**：同一个 `server.py` 可以配多次，用不同 `env`（不同 key / base_url）区分，界面里显示成两张独立卡片。

---

## 目录结构

```
.
├── server.py                   # 全部逻辑：key 解析 + 两个工具 + 错误归一
├── test_client.py              # 冒烟测试（官方 mcp 客户端直连 stdio）
├── requirements.txt            # mcp>=1.0.0
├── examples/
│   └── qwenpaw-driver.yaml     # 可直接改路径用的 driver 配置
├── AGENTS.md                   # 给 AI 编码助手的改动约定
├── LICENSE                     # MIT
└── README.md
```

---

## 安全

- key 只通过环境变量 / 文件 / QwenPaw secret 注入，代码里没有硬编码凭据。
- `.gitignore` 覆盖 `.env`、`.venv/`、`__pycache__/`。
- 服务器不读写你的本地文件（除读取 key 的那一次），只向 `api.deepseek.com` 发请求。

## License

[MIT](LICENSE)

---

*Standard stdio MCP server — it happens to be built for QwenPaw, but any MCP client can run `python server.py`.*
