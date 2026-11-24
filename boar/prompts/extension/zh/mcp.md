# MCP 桥接（stdio）

> 用途：开发者中文参考版；LLM 消费英文版，如有出入以英文版为准。
> 位置：boar/prompts/extension/mcp.md（英文权威源）

把外部 MCP server（Model Context Protocol，stdio 传输）的工具注入本系统：manifest 声明 `contributes.mcp_servers`；loader 拉起子进程、完成 MCP 握手、列出其工具并逐个注册为 BoarAi 工具（命名 `mcp_<server>_<tool>`），调用经 JSON-RPC 转发。扩展卸载时子进程自动停止。

```yaml
contributes:
  mcp_servers:
    - name: fs_server            # snake_case；成为工具名前缀
      command: npx               # 启动命令（stdio 传输）
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
      env: {}                    # 可选环境变量
      timeout: 30                # 握手/list 超时（秒）
      tool_timeout: 120          # 单次工具调用超时（秒）
```

## 行为约定

- 注册工具 `security_profile: none`（外部 server 视为用户配置的可信代码）+ `suggested_category: dangerous`（默认仍走审批流）
- handler 返回 `{"result": "<text>"}`；server 出错返回 `{"error": ...}`
- 单个 server 启动失败只记 error —— 扩展其余部分照常加载
- `extension_type: mcp` 仍可作目录标记，但 MCP 能力与类型无关 —— **任何扩展都能桥接 MCP server**

## Pitfalls

- Windows：子进程必须隐藏控制台窗口（CREATE_NO_WINDOW），否则服务器 DETACHED 运行时黑窗闪烁 —— 桥接客户端（core/extension/mcp/）已处理，自建子进程时注意
- `command` 用绝对路径或确保在服务器进程 PATH 中（GUI 启动的进程 PATH 可能与终端不同）
- env 中勿硬编码密钥；敏感配置走 `.boarai/config/`

## 参考实现

`core/extension/mcp/` —— stdio 桥接客户端（握手、工具列举、JSON-RPC 转发、子进程生命周期）
