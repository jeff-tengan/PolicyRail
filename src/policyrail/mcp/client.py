from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from .._version import __version__
from .models import MCPTool, MCPToolResult

DEFAULT_MCP_PROTOCOL_VERSION = "2025-11-25"


class MCPTransportSessionExpired(RuntimeError):
    pass


class MCPTransport(Protocol):
    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class MCPClient(Protocol):
    def list_tools(self) -> list[MCPTool]:
        ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPToolResult:
        ...


class JSONRPCMCPClient:
    def __init__(
        self,
        transport: MCPTransport,
        *,
        client_name: str = "policyrail",
        client_version: str = __version__,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
        capabilities: dict[str, Any] | None = None,
        auto_initialize: bool = True,
    ) -> None:
        self.transport = transport
        self.client_name = client_name
        self.client_version = client_version
        self.protocol_version = protocol_version
        self.capabilities = dict(capabilities or {})
        self.auto_initialize = auto_initialize
        self._initialized = False
        self.negotiated_protocol_version: str | None = None
        self.server_capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.server_instructions: str | None = None

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {
                "protocolVersion": self.negotiated_protocol_version,
                "capabilities": dict(self.server_capabilities),
                "serverInfo": dict(self.server_info),
                "instructions": self.server_instructions,
            }

        result = self.transport.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": dict(self.capabilities),
                "clientInfo": {
                    "name": self.client_name,
                    "version": self.client_version,
                },
            },
        )
        self.negotiated_protocol_version = str(
            result.get("protocolVersion", self.protocol_version)
        )
        self.server_capabilities = _ensure_dict(result.get("capabilities"))
        self.server_info = _ensure_dict(result.get("serverInfo"))
        instructions = result.get("instructions")
        self.server_instructions = str(instructions) if isinstance(instructions, str) else None

        if hasattr(self.transport, "set_protocol_version"):
            self.transport.set_protocol_version(self.negotiated_protocol_version)

        self.transport.request("notifications/initialized", {})
        self._initialized = True
        return result

    def list_tools(self) -> list[MCPTool]:
        self._ensure_initialized()
        tools: list[MCPTool] = []
        cursor: str | None = None

        while True:
            params = {"cursor": cursor} if cursor else {}
            payload = self._request_with_retry("tools/list", params)
            tools.extend(self._coerce_tool(item) for item in payload.get("tools", []))
            next_cursor = payload.get("nextCursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)

        return tools

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPToolResult:
        self._ensure_initialized()
        payload = self._request_with_retry(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        return self._coerce_result(payload)

    def close(self) -> None:
        if hasattr(self.transport, "close"):
            self.transport.close()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self.auto_initialize and not self._initialized:
            self.initialize()

    def _request_with_retry(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.transport.request(method, params)
        except MCPTransportSessionExpired:
            self._initialized = False
            self.initialize()
            return self.transport.request(method, params)

    @staticmethod
    def _coerce_tool(raw: dict[str, Any]) -> MCPTool:
        return MCPTool(
            name=str(raw["name"]),
            description=str(raw.get("description", "")),
            input_schema=_ensure_dict(raw.get("inputSchema")),
            annotations=_ensure_dict(raw.get("annotations")),
            metadata=_ensure_dict(raw.get("metadata")),
        )

    @staticmethod
    def _coerce_result(raw: dict[str, Any]) -> MCPToolResult:
        content = raw.get("content", [])
        normalized_content: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                normalized_content.append(dict(item))
            elif item is not None:
                normalized_content.append({"type": "text", "text": str(item)})

        return MCPToolResult(
            content=normalized_content,
            structured_content=raw.get("structuredContent"),
            is_error=bool(raw.get("isError", False)),
            metadata=_ensure_dict(raw.get("metadata")),
        )


class InMemoryMCPTransport:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[MCPTool, Callable[[dict[str, Any]], Any]]] = {}
        self._protocol_version = DEFAULT_MCP_PROTOCOL_VERSION
        self._initialized = False

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        handler: Callable[[dict[str, Any]], Any],
        input_schema: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        tool = MCPTool(
            name=name,
            description=description,
            input_schema=dict(input_schema or {}),
            annotations=dict(annotations or {}),
            metadata=dict(metadata or {}),
        )
        self._tools[name] = (tool, handler)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "initialize":
            payload = dict(params or {})
            self._protocol_version = str(payload.get("protocolVersion", DEFAULT_MCP_PROTOCOL_VERSION))
            return {
                "protocolVersion": self._protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "in-memory-mcp", "version": "1.0.0"},
                "instructions": "Test transport for local MCP integration.",
            }

        if method == "notifications/initialized":
            self._initialized = True
            return {}

        if method == "tools/list":
            if not self._initialized:
                raise RuntimeError("Transporte MCP em memoria ainda nao foi inicializado.")
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                        "annotations": tool.annotations,
                        "metadata": tool.metadata,
                    }
                    for tool, _handler in self._tools.values()
                ]
            }

        if method == "tools/call":
            if not self._initialized:
                raise RuntimeError("Transporte MCP em memoria ainda nao foi inicializado.")

            payload = dict(params or {})
            name = str(payload.get("name", "")).strip()
            if name not in self._tools:
                raise KeyError(f"Tool MCP nao encontrada: {name}")

            _tool, handler = self._tools[name]
            raw_result = handler(dict(payload.get("arguments") or {}))
            result = _coerce_in_memory_result(raw_result)
            return {
                "content": result.content,
                "structuredContent": result.structured_content,
                "isError": result.is_error,
                "metadata": result.metadata,
            }

        raise ValueError(f"Metodo MCP nao suportado: {method}")

    def set_protocol_version(self, protocol_version: str) -> None:
        self._protocol_version = protocol_version


def _coerce_in_memory_result(value: Any) -> MCPToolResult:
    if isinstance(value, MCPToolResult):
        return value

    if isinstance(value, str):
        return MCPToolResult(content=[{"type": "text", "text": value}])

    if isinstance(value, list):
        content: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                content.append(dict(item))
            else:
                content.append({"type": "text", "text": str(item)})
        return MCPToolResult(content=content)

    if isinstance(value, dict):
        looks_like_result = any(
            key in value
            for key in ("content", "structuredContent", "structured_content", "isError", "is_error")
        )
        if looks_like_result:
            raw_content = value.get("content", [])
            content: list[dict[str, Any]] = []
            for item in raw_content:
                if isinstance(item, dict):
                    content.append(dict(item))
                else:
                    content.append({"type": "text", "text": str(item)})
            return MCPToolResult(
                content=content,
                structured_content=value.get("structuredContent", value.get("structured_content")),
                is_error=bool(value.get("isError", value.get("is_error", False))),
                metadata=_ensure_dict(value.get("metadata")),
            )

        return MCPToolResult(
            content=[{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            structured_content=value,
        )

    return MCPToolResult(content=[{"type": "text", "text": str(value)}])


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}
