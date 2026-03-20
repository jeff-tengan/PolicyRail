from __future__ import annotations

from .client import MCPClient
from .models import MCPToolPolicy
from ..core.models import ToolCall, ToolExecutionResult, ToolSpec


class MCPToolRegistry:
    def __init__(
        self,
        client: MCPClient,
        *,
        default_policy: MCPToolPolicy | None = None,
        tool_policies: dict[str, MCPToolPolicy] | None = None,
    ) -> None:
        self.client = client
        self.default_policy = default_policy or MCPToolPolicy()
        self.tool_policies = dict(tool_policies or {})

    def build_tool_specs(self) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for tool in self.client.list_tools():
            policy = self.tool_policies.get(tool.name, self.default_policy)
            specs.append(
                ToolSpec(
                    name=tool.name,
                    description=policy.description or tool.description or f"MCP tool '{tool.name}'.",
                    sensitive=policy.sensitive,
                    requires_human_approval=policy.requires_human_approval,
                    max_risk_score=policy.max_risk_score,
                )
            )
        return specs


class MCPToolExecutor:
    def __init__(self, client: MCPClient, *, server_name: str = "mcp") -> None:
        self.client = client
        self.server_name = server_name

    def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        result = self.client.call_tool(tool_call.name, tool_call.arguments)
        output = {
            "content": [dict(item) for item in result.content],
            "structured_content": result.structured_content,
            "text": result.text_content(),
        }
        return ToolExecutionResult(
            tool_name=tool_call.name,
            arguments=dict(tool_call.arguments),
            success=not result.is_error,
            output=output,
            metadata={
                "executor": "mcp",
                "server_name": self.server_name,
                **dict(result.metadata),
            },
        )
