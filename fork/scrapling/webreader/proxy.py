"""Expose tools of another MCP server (over a ClientSession) as tools of an MCPServer, keeping
their names, descriptions and input schemas. Used for web-reader's tools inside the shared server,
and by the stdio shim that forwards Claude's calls to the shared HTTP server."""

import asyncio
from typing import Any, Callable, Dict, Iterable, Optional

from mcp.server.mcpserver.tools.base import Tool
from mcp.types import CallToolResult, TextContent


class HeldSession:
    """A ClientSession kept open by its own task. MCP transports are anyio task groups that must be
    exited by the task that entered them, so a connection opened inside a request handler (which
    ends) would crash the server; here a background task owns it and requests only use it."""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect  # () -> async context manager yielding an initialized ClientSession
        self._session: Any = None
        self._stop: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def _hold(self, ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            async with self._connect() as session:
                ready.set_result(session)
                await stop.wait()
        except BaseException as e:  # noqa: BLE001 - reported to the waiting caller
            if not ready.done():
                ready.set_exception(e)
        finally:
            self._session = None

    async def get(self) -> Any:
        async with self._lock:
            if self._session is None:
                loop = asyncio.get_running_loop()
                ready: asyncio.Future = loop.create_future()
                self._stop = asyncio.Event()
                self._task = loop.create_task(self._hold(ready, self._stop))
                self._session = await ready
            return self._session

    async def close(self) -> None:
        if self._stop:
            self._stop.set()
        if self._task:
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass
        self._session = self._task = None

    async def call(self, name: str, args: Dict[str, Any]) -> Any:
        try:
            return await (await self.get()).call_tool(name, args)
        except Exception:
            await self.close()  # the other side restarted or exited: connect again once
            return await (await self.get()).call_tool(name, args)


async def _placeholder(**kwargs: Any) -> None:  # the schema comes from the remote tool
    return None


class ProxyTool(Tool):
    call: Callable[[str, Dict[str, Any]], Any]

    async def run(self, arguments: dict, context: Any, convert_result: bool = False) -> Any:
        try:
            result = await self.call(self.name, arguments or {})
        except Exception as e:  # report like a tool error, keep the server up
            return CallToolResult(content=[TextContent(type="text", text=f"{self.name} 실패: {str(e).splitlines()[0][:300] if str(e) else type(e).__name__}")], is_error=True)
        if isinstance(result, CallToolResult):
            return result
        return CallToolResult(content=list(result.content), structured_content=getattr(result, "structured_content", None), is_error=getattr(result, "is_error", False))


def add_proxy_tools(server: Any, tools: Iterable[Any], call: Callable[[str, Dict[str, Any]], Any], only: Optional[set] = None, rename: Optional[Dict[str, str]] = None) -> list:
    """Register remote tools (mcp.types.Tool list) on an MCPServer; returns the names added."""
    added = []
    manager = server._tool_manager
    for t in tools:
        if only is not None and t.name not in only:
            continue
        base = Tool.from_function(_placeholder, name=t.name, description=t.description or "", annotations=t.annotations, structured_output=False)
        proxy = ProxyTool(**{**base.__dict__, "parameters": t.input_schema or {"type": "object"}}, call=call)
        manager._tools[t.name] = proxy
        added.append(t.name)
    return added
