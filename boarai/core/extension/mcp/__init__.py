"""MCP (Model Context Protocol) bridge for the BoarAi extension system.

Bridges external MCP servers into BoarAi's tool registry: an extension
declares ``contributes.mcp_servers`` in its manifest; the loader starts a
:class:`StdioMCPClient` per server, lists its tools, and registers each as a
BoarAi tool whose handler forwards to ``tools/call``.

Transport: stdio with newline-delimited JSON-RPC 2.0 (the MCP default for
local servers). One client instance owns one subprocess.
"""

from .client import StdioMCPClient, MCPClientError

__all__ = ["StdioMCPClient", "MCPClientError"]
