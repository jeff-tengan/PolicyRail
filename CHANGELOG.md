# Changelog

All notable changes to this project will be documented in this file.

## 0.5.0 - 2026-03-20

- added a generic MCP layer for tool discovery, allowlisting, and execution
- added `StdioMCPTransport` and `StreamableHTTPMCPTransport`
- extended the secure pipeline with optional tool execution and structured tool results
- expanded audit logging to record MCP execution metadata
- refreshed bilingual documentation and public package metadata

## 0.4.0 - 2026-03-20

- added multi-provider preflight adapters for OpenAI, Azure OpenAI, Anthropic, Google Gen AI, and Bedrock
- added provider factory selection by environment variables
- improved package exports and documentation for provider-agnostic usage
