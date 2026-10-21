# AGENTS.md

本文件说明在本仓库中修改代码时需要注意的事项。面向用户的使用说明见 [README.md](README.md)。

## 项目说明

这是一个 MCP stdio 服务器，供 QwenPaw 使用。它把 DeepSeek 官方 Search / Fetch API 封装为 `deepseek_search` 和 `deepseek_fetch` 两个工具。全部逻辑在 `server.py` 中，没有数据库和前端。

当 QwenPaw 已经配置 DeepSeek 提供商时，用户不需要填写 key：服务器会读取 `~/.qwenpaw.secret` 中的密文并解密。改动代码时请保留这条链路，相关说明写在 `server.py` 的模块 docstring 里。

## 三条规则

1. stdout 只用于 jsonrpc。
   调试信息必须写到 stderr，例如 `print(..., file=sys.stderr)`。写入 stdout 会导致 QwenPaw 的 MCP 客户端卡在连接中，或报 JSON 解析错误。

2. 工具 schema 保持向后兼容。
   FastMCP 根据函数签名和 docstring 生成 JSON Schema。已有参数可以新增可选项或默认值，但不能改名、改类型，也不能把可选参数改为必填，否则已经写好的提示词会失效。

3. 不硬编码凭据。
   key 只能来自 `resolve_api_key()` 的来源列表，不要写进代码、示例、测试或提交信息。

## 目录

```
server.py        key 解析、_post、两个 @mcp.tool
test_client.py   冒烟测试，使用官方 mcp 客户端直连 stdio
examples/        QwenPaw driver 配置模板
README.md        使用说明
```

## 修改流程

```bash
# 检查 key 来源，不产生调用费用
python server.py --check-key

# 完整测试，会产生两次 API 调用，约 ¥0.01
python test_client.py

# 提交前执行隐私检查，见下文
```

`test_client.py` 会检查三件事：工具名列表、search 返回包含 `results`、fetch 返回非空 `content`。修改解析逻辑后需要运行它。

## key 解析顺序

1. 环境变量 `DEEPSEEK_API_KEY`
2. `DEEPSEEK_API_KEY_FILE` 文件的第一个非空行
3. `_from_qwenpaw_provider()`，读取 `~/.qwenpaw.secret/providers/builtin/deepseek.json` 的 `api_key`
4. 返回错误信息，说明尝试过的来源

第 3 级用于支持已配置 DeepSeek 提供商的 QwenPaw 用户。`ENC:` 前缀表示 QwenPaw 的密文格式，需要调用 `qwenpaw.security.secret_store.decrypt` 解密。解密失败时返回空字符串并继续尝试下一个来源，不要抛出异常，否则会影响没有配置 provider 的环境。

## 返回格式

- 成功：`{"results": [...]}` 或 `{"title": ..., "content": ...}`
- 失败：`{"error": "...", "detail": ...}`

这两层的键名不要修改。错误信息需要让模型能判断下一步操作（更换 key、充值或降低频率），不要统一替换为 `"failed"`。

`deepseek_search` 在返回前会裁剪字段，只保留 8 个。原因是上游返回字段较多，全部放进上下文会占用较多 token。新增字段前先确认必要性。

## 隐私检查

提交前执行：

```bash
grep -rn -iE "sk-[a-zA-Z0-9]{10,}|/root/|/home/[a-z]|password|BEGIN .*PRIVATE" . \
  --exclude-dir=.git --exclude=AGENTS.md
```

- 示例中的路径统一写 `/absolute/path/to/...`。
- key 统一写 `sk-xxxxxxxx` 占位符。
- 不要包含个人邮箱、姓名、机器名、内网地址或聊天截图。
- README 中的 `~/.qwenpaw.secret/...` 是 QwenPaw 的约定路径，不属于个人信息，需要保留。

## 发布

- 版本号遵循语义化：schema 或行为有变化时递增 minor，只改文档或错误文案时递增 patch。目前没有打 tag。
- 发布前确认 README 中「工作原理」一节与代码一致，避免文档与实现不符。

## 已知问题

| 问题 | 说明 |
|---|---|
| 把 `ENC:` 当明文使用 | 会返回 401，且错误信息看不出原因，可用 `--check-key` 确认来源 |
| QwenPaw 管理页不自动刷新 | 从外部修改 yaml 后需要手动刷新页面 |
| 旧会话没有新工具 | MCP 工具在会话启动时注入，安装后需要新建会话 |
| 相对路径不可用 | QwenPaw 启动子进程时工作目录不确定，`command` 和 `args` 需要使用绝对路径 |
| `need_content=True` 且 `count=50` | 合法，但返回内容会占用大量上下文，建议 count 不超过 5 |
