# MCP Bridging (stdio)

> Purpose: file_read this file when injecting tools from an external MCP
> server (read common.md first).
> Location: boar/prompts/extension/mcp.md

Inject tools from external MCP servers (Model Context Protocol, stdio
transport) into this system: declare contributes.mcp_servers in the
manifest; the loader spawns the subprocess, completes the MCP handshake,
lists its tools, and registers each as a BoarAi tool (named
mcp_<server>_<tool>), forwarding calls over JSON-RPC. The subprocess is
stopped automatically when the extension unmounts.

```yaml
contributes:
  mcp_servers:
    - name: fs_server            # snake_case; becomes the tool-name prefix
      command: npx               # launch command (stdio transport)
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
      env: {}                    # optional environment variables
      timeout: 30                # handshake/list timeout (seconds)
      tool_timeout: 120          # per tool-call timeout (seconds)
```

## Behavior contract

- Registered tools get security_profile: none (external servers are
  user-configured trusted code) + suggested_category: dangerous (still
  gated by approval flows by default)
- Handlers return {"result": "<text>"}; server errors return {"error": ...}
- A single server failing to start only logs an error — the rest of the
  extension still loads
- extension_type: mcp still works as a directory marker, but MCP capability
  is type-independent — any extension can bridge MCP servers

## Pitfalls

- Windows: spawned subprocesses must hide console windows
  (CREATE_NO_WINDOW), or black windows flash when the server runs detached —
  the bridge client (core/extension/mcp/) already handles this; mind it if
  you spawn your own subprocess
- command must be an absolute path or resolvable in the server process PATH
  (GUI-launched processes may have a different PATH than a terminal)
- Never hardcode secrets in env; sensitive config goes through
  .boarai/config/

## Reference implementation

core/extension/mcp/ — stdio bridge client (handshake, tool listing,
JSON-RPC forwarding, subprocess lifecycle)
