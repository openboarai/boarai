"""Stdio MCP client — newline-delimited JSON-RPC 2.0 over a subprocess.

Wire format (one JSON object per line on stdout/stdin):
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {...}}
    {"jsonrpc": "2.0", "id": 1, "result": {...}}

Notifications from the server (no "id") are skipped; stderr is drained by a
background task so a chatty server never deadlocks on a full pipe.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JSONRPC = "2.0"

# The initialize params BoarAi advertises. protocolVersion "2024-11-05" is the
# MCP stdio baseline; servers negotiate within it.
_CLIENT_INFO = {"name": "boarai", "version": "1.6.1"}
_PROTOCOL_VERSION = "2024-11-05"


class MCPClientError(Exception):
    """Raised when the MCP server fails or the protocol is violated."""


class StdioMCPClient:
    """One MCP server subprocess; sequential request/response correlation."""

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self._timeout = timeout
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = itertools.count(1)
        self._stderr_task: Optional[asyncio.Task] = None
        self._closed = False

    # --- Lifecycle ---

    async def start(self) -> None:
        """Spawn the subprocess and complete the MCP initialize handshake."""
        if self._proc is not None:
            return
        try:
            # Windows: the server runs detached (no console) — without
            # CREATE_NO_WINDOW every spawned MCP server would flash a new
            # console window on the user's screen.
            popen_kwargs: Dict[str, Any] = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self._proc = await asyncio.create_subprocess_exec(
                self._command, *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                **popen_kwargs,
            )
        except Exception as e:
            raise MCPClientError(
                f"Failed to spawn MCP server '{self._command}': {e}"
            ) from e
        # Drain stderr in the background (prevents pipe-full deadlocks).
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        # MCP initialize handshake
        result = await self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        if not isinstance(result, dict):
            raise MCPClientError("MCP initialize returned a non-object result")
        # Required notification before any other call
        await self._notify("notifications/initialized")
        logger.info(
            "MCP server ready (command=%s, server=%s)",
            self._command,
            (result.get("serverInfo") or {}).get("name", "unknown"),
        )

    async def close(self) -> None:
        """Terminate the subprocess (idempotent, best-effort)."""
        if self._closed:
            return
        self._closed = True
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._proc is not None:
            try:
                if self._proc.returncode is None:
                    self._proc.terminate()
                    try:
                        await asyncio.wait_for(self._proc.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        self._proc.kill()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass
            self._proc = None

    @property
    def running(self) -> bool:
        return (
            self._proc is not None
            and self._proc.returncode is None
            and not self._closed
        )

    # --- MCP surface ---

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Return the server's tool manifests (name/description/inputSchema)."""
        result = await self._rpc("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke a tool; returns {"content": str, "is_error": bool}."""
        result = await self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if not isinstance(result, dict):
            return {"content": "", "is_error": False}
        # MCP content blocks: [{"type": "text", "text": ...}, ...]
        parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return {
            "content": "\n".join(parts),
            "is_error": bool(result.get("isError", False)),
        }

    # --- JSON-RPC plumbing ---

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        """Send a request and await the matching response line."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MCPClientError("MCP client not started")
        rid = next(self._next_id)
        frame = {"jsonrpc": _JSONRPC, "id": rid, "method": method, "params": params}
        try:
            self._proc.stdin.write(
                (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            )
            await self._proc.stdin.drain()
        except Exception as e:
            raise MCPClientError(f"MCP write failed ({method}): {e}") from e

        try:
            return await asyncio.wait_for(
                self._read_response(rid), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            raise MCPClientError(f"MCP timeout ({self._timeout}s) on {method}")

    async def _notify(self, method: str) -> None:
        """Fire-and-forget notification (no id, no response expected)."""
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("MCP client not started")
        frame = {"jsonrpc": _JSONRPC, "method": method}
        try:
            self._proc.stdin.write(
                (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            )
            await self._proc.stdin.drain()
        except Exception as e:
            raise MCPClientError(f"MCP notify failed ({method}): {e}") from e

    async def _read_response(self, rid: int) -> Any:
        """Read stdout lines until the response with our id arrives."""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise MCPClientError("MCP server closed stdout")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue  # server banner / non-JSON noise
            if not isinstance(msg, dict):
                continue
            if msg.get("id") != rid:
                continue  # server-initiated notification or other traffic
            if "error" in msg and msg["error"]:
                err = msg["error"]
                raise MCPClientError(
                    f"MCP error: {err.get('message', err)} (code {err.get('code')})"
                )
            return msg.get("result")

    async def _drain_stderr(self) -> None:
        """Consume the server's stderr so its pipe never fills."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                logger.debug(
                    "MCP[%s] stderr: %s",
                    self._command,
                    line.decode("utf-8", errors="replace").rstrip(),
                )
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            return
