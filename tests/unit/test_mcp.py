from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from policyrail import (
    JSONRPCMCPClient,
    JsonAuditLogger,
    LLMResponse,
    MCPToolExecutor,
    MCPToolPolicy,
    MCPToolRegistry,
    PolicyEngine,
    SecureGenAIPipeline,
    SecureRequest,
    ToolCall,
)
from policyrail.mcp import InMemoryMCPTransport
from policyrail.templates.system_policies import DEFAULT_SYSTEM_POLICY


class _MCPSearchAdapter:
    def generate(self, envelope):
        return LLMResponse(
            text="Consultando base aprovada via MCP.",
            tool_call=ToolCall(
                name="search_policy_docs",
                arguments={"query": envelope.user_input, "top_k": 2},
            ),
        )


class MCPIntegrationTests(unittest.TestCase):
    def test_jsonrpc_client_lists_and_calls_tools(self) -> None:
        transport = InMemoryMCPTransport()
        transport.register_tool(
            name="search_policy_docs",
            description="Busca documentos aprovados.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=lambda arguments: {
                "content": [{"type": "text", "text": f"Consulta: {arguments['query']}"}],
                "structuredContent": {"ok": True},
                "metadata": {"server": "policy-kb"},
            },
        )
        client = JSONRPCMCPClient(transport)

        tools = client.list_tools()
        result = client.call_tool("search_policy_docs", {"query": "password policy"})

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "search_policy_docs")
        self.assertIn("Consulta", result.text_content())
        self.assertEqual(result.structured_content, {"ok": True})

    def test_registry_builds_tool_specs_with_overrides(self) -> None:
        transport = InMemoryMCPTransport()
        transport.register_tool(
            name="search_policy_docs",
            description="Busca documentos aprovados.",
            handler=lambda _arguments: "ok",
        )
        transport.register_tool(
            name="delete_policy_record",
            description="Remove um registro de policy.",
            handler=lambda _arguments: "blocked",
        )
        client = JSONRPCMCPClient(transport)
        registry = MCPToolRegistry(
            client,
            tool_policies={
                "search_policy_docs": MCPToolPolicy(
                    sensitive=False,
                    requires_human_approval=False,
                    max_risk_score=35,
                )
            },
        )

        specs = {tool.name: tool for tool in registry.build_tool_specs()}

        self.assertFalse(specs["search_policy_docs"].sensitive)
        self.assertFalse(specs["search_policy_docs"].requires_human_approval)
        self.assertTrue(specs["delete_policy_record"].sensitive)
        self.assertTrue(specs["delete_policy_record"].requires_human_approval)

    def test_pipeline_executes_allowed_mcp_tool(self) -> None:
        transport = InMemoryMCPTransport()
        transport.register_tool(
            name="search_policy_docs",
            description="Busca documentos aprovados.",
            handler=lambda arguments: {
                "content": [
                    {"type": "text", "text": "Password Policy"},
                    {"type": "text", "text": f"Query: {arguments['query']}"},
                ],
                "structuredContent": {
                    "matches": [
                        {
                            "title": "Password Policy",
                            "document_id": "password_policy",
                        }
                    ]
                },
                "metadata": {"backing_store": "in-memory-kb"},
            },
        )
        client = JSONRPCMCPClient(transport)
        registry = MCPToolRegistry(
            client,
            tool_policies={
                "search_policy_docs": MCPToolPolicy(
                    sensitive=False,
                    requires_human_approval=False,
                    max_risk_score=35,
                )
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = SecureGenAIPipeline(
                llm_adapter=_MCPSearchAdapter(),
                policy_engine=PolicyEngine(registry.build_tool_specs()),
                tool_executor=MCPToolExecutor(client, server_name="policy-kb"),
                audit_logger=JsonAuditLogger(Path(tmpdir) / "audit.jsonl"),
            )
            response = pipeline.process(
                SecureRequest(
                    user_input="Qual e a politica de senha corporativa?",
                    system_instruction=DEFAULT_SYSTEM_POLICY,
                    trusted_context=["Base de politicas aprovada"],
                )
            )

        self.assertEqual(response.status, "allow")
        self.assertIsNotNone(response.tool_call)
        self.assertIsNotNone(response.tool_result)
        self.assertTrue(response.tool_result.success)
        self.assertEqual(response.tool_result.metadata["server_name"], "policy-kb")
        self.assertEqual(
            response.tool_result.output["structured_content"]["matches"][0]["title"],
            "Password Policy",
        )
        self.assertTrue(response.model_metadata["tool_execution"]["success"])


if __name__ == "__main__":
    unittest.main()
