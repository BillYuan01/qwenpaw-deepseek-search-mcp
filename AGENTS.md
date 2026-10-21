# AGENTS.md

给在这个仓库里干活的 AI 编码助手（以及未来的自己）的约定。人类读者请看 [README.md](README.md)。

## 这个仓库是什么

一个 **MCP stdio 服务器**，给 **QwenPaw** 用：把 DeepSeek 官方 Search / Fetch API 暴露成 `deepseek_search` 和 `deepseek_fetch` 两个工具。全部逻辑在 `server.py` 单文件里，没有框架、没有数据库、没有前端。

它跟通用搜索 MCP 的区别只有一点：**QwenPaw 已配好 DeepSeek 提供商时，key 免填**——读 `~/.qwenpaw.secret` 里的密文并解密。这条链路是这个项目最该维持的东西，改代码前先读 `server.py` 的模块 docstring。

## 三条铁律

1. **stdout 只属于 jsonrpc。**
   任何调试输出必须走 `print(..., file=sys.stderr)`。往 stdout 多打一个字符，QwenPaw 那边的 MCP 客户端会卡在「连接中」或报 JSON 解析错误，而且极难排查。

2. **工具 schema 向后兼容。**
   FastMCP 从函数签名 + docstring 生成 JSON Schema。已有参数只能「加可选参数」或「加默认值」，不能改名、不能改类型、不能把可选变必填——否则已经写好的提示词会静默失效。

3. **不硬编码任何凭据。**
   key 只能来自 `resolve_api_key()` 的四级来源。不要把 key 写进代码、示例、测试、提交信息、issue 回复。

## 目录

```
server.py        全部实现：key 解析、_post、两个 @mcp.tool
test_client.py   冒烟测试，用官方 mcp 客户端直连 stdio
examples/        QwenPaw driver 配置模板
README.md        面向用户：原理、安装、排障
```

## 改动流程

```bash
# 1. 改完先自检 key 链路（不花钱）
python server.py --check-key

# 2. 跑完整冒烟测试（约 ¥0.01，会真实调用 search + fetch）
python test_client.py

# 3. 提交前扫一遍隐私（见下）
```

`test_client.py` 断言三件事：工具名集合、search 返回含 `results`、fetch 返回非空 `content`。改了解析逻辑就跑它，不要靠「读过代码觉得没问题」。

## Key 解析顺序（改动时保持这个优先级）

1. `DEEPSEEK_API_KEY` 环境变量
2. `DEEPSEEK_API_KEY_FILE` 文件首个非空行
3. `_from_qwenpaw_provider()` → `~/.qwenpaw.secret/providers/builtin/deepseek.json` 的 `api_key`
4. 返回结构化错误，说明试过哪些来源

第 3 级的语义是「已配过 DeepSeek 提供商的 QwenPaw 用户免填 key」。`ENC:` 前缀是 QwenPaw 的密文格式，必须经 `qwenpaw.security.secret_store.decrypt` 解密；解不开**不要报错**，静默跳到下一级，否则没配 provider 的机器会被误伤。

## 返回格式约定

- 成功：`{"results": [...]}` 或 `{"title": ..., "content": ...}`
- 失败：`{"error": "...", "detail": ...}`

不要改这两层的键名。错误信息要能让模型自己判断下一步（换 key、充值、还是降速），不要吞成 `"failed"`。

`deepseek_search` 返回前会做字段瘦身（只留 8 个字段），这是刻意的：上游字段太多，整包塞进上下文会浪费 token。新增字段请评估必要性。

## 隐私红线

提交前必须跑：

```bash
grep -rn -iE "sk-[a-zA-Z0-9]{10,}|/root/|/home/[a-z]|password|BEGIN .*PRIVATE" . \
  --exclude-dir=.git --exclude=AGENTS.md
```

- 示例里的路径一律写 `/absolute/path/to/...`，不写本机真实路径。
- key 一律写 `sk-xxxxxxxx` 占位符。
- 不放个人邮箱、姓名、机器名、内网地址、聊天截图。
- README 提到 QwenPaw 的 secret 路径 `~/.qwenpaw.secret/...` 是**产品约定路径**，不是个人信息，必须保留（它是第 3 级解析的依据）。

## 发布

- 版本语义化：改 schema / 行为 → minor；只改文档或错误文案 → patch。当前无 tag。
- 发布前确认 README 的「工作原理」四节与代码一致——它们是这个项目最有价值的部分，过期比没有更糟。

## 已知坑

| 坑 | 说明 |
|---|---|
| `ENC:` 当成明文用 | 会拿到 401，且报错看不出原因；`--check-key` 能看出来源不对 |
| QwenPaw 管理页不自动刷新 | 外部改了 yaml，页面要手动刷新才显示新卡片 |
| 老会话拿不到新工具 | MCP 工具在会话启动时注入，装完要开新会话 |
| 相对路径 | QwenPaw 拉起子进程时 cwd 不确定，`command` / `args` 一律绝对路径 |
| `need_content=True` + `count=50` | 合法但上下文会爆；建议 count 降到 5 以内 |
